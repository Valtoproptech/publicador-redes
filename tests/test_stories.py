"""Stories (Formato = Story): reglas, adaptadores de Instagram y Facebook y motor con dobles en memoria."""
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from PIL import Image

from publicador import notion as N
from publicador.config import Destino
from publicador.errores import ErrorPermanente, ErrorValidacion, ResultadoIncierto
from publicador.esquema import C, P
from publicador.imagenes import Secuencia, preparar_foto
from publicador.motor import Medios, Motor
from publicador.redes.base import REINTENTAR, Adaptador, Resultado, publicar_en_orden
from publicador.redes.facebook import Facebook
from publicador.redes.instagram import Instagram
from publicador.reglas import duracion_story_fuera, planificar
from tests.test_fotos import CARPETA, DriveFalso, NotionConArchivos, foto, meta
from tests.test_motor import T0, Base
from tests.test_redes import T, Resp, SesionFalsa, archivo

STORY = {C.FORMATO: N.opcion("Story")}


def video_meta(nombre, segundos):
    return {"id": "id-" + nombre, "name": nombre, "mimeType": "video/mp4",
            "videoMediaMetadata": {"durationMillis": str(int(segundos * 1000))}}


class Reglas(unittest.TestCase):
    def test_facebook_no_programa_stories(self):
        base = dict(red="Facebook", via="nativo", estado="En cola", anticipacion=timedelta(minutes=45))
        self.assertEqual(planificar(**base, cuando=T0, ahora=T0 - timedelta(days=2)).accion, "programar_nativo")
        self.assertEqual(planificar(**base, cuando=T0, ahora=T0 - timedelta(days=2), story=True).accion, "esperar")
        self.assertEqual(planificar(**base, cuando=T0, ahora=T0 - timedelta(minutes=30), story=True).accion,
                         "publicar_a_la_hora")

    def test_duracion(self):
        self.assertIsNone(duracion_story_fuera(60, "Instagram"))
        self.assertIn("3 a 60", duracion_story_fuera(75, "Instagram"))
        self.assertIsNone(duracion_story_fuera(75, "Facebook"))
        self.assertIn("3 a 90", duracion_story_fuera(2, "Facebook"))

    def test_foto_vertical_sirve_como_story(self):
        tmp = Path(tempfile.mkdtemp())
        ruta = tmp / "s.jpg"
        Image.new("RGB", (1080, 1920), (10, 20, 30)).save(ruta, "JPEG")
        f = preparar_foto(ruta, "s.jpg", "Instagram Story", tmp)
        self.assertEqual(f.ruta, ruta)                       # 9:16: intacta (en el feed sería error)
        png = tmp / "s.png"
        Image.new("RGBA", (1080, 1920), (10, 20, 30, 255)).save(png, "PNG")
        self.assertIn("Convertida a JPEG", preparar_foto(png, "s.png", "Instagram Story", tmp).detalle)
        self.assertEqual(preparar_foto(png, "s.png", "Facebook Story", tmp).ruta, png)

    def test_publicar_en_orden(self):
        def falla():
            raise ErrorPermanente("rechazada")
        with self.assertRaises(ErrorPermanente):             # la primera falla: error normal, nada salió
            publicar_en_orden("Instagram", [falla])
        with self.assertRaises(ResultadoIncierto) as e:      # ya salió una: nunca reintentar solo
            publicar_en_orden("Instagram", [lambda: "M1", falla, lambda: "M3"])
        self.assertIn("1 de 3", str(e.exception))


class MediosStory(unittest.TestCase):
    def revisar(self, archivos, redes=("Instagram", "Facebook"), carpeta=True, file_id="x"):
        c = type("C", (), {"video_id": file_id, "carpeta": carpeta, "redes": list(redes), "story": True})()
        return Medios(DriveFalso(archivos), Path(tempfile.mkdtemp())).story(c)

    def test_fotos_y_videos_en_orden(self):
        piezas = self.revisar([video_meta("2.mp4", 15), meta("10.jpg", alto=1920), meta("1.png", "image/png"),
                               meta("notas.txt", "text/plain")])
        self.assertEqual([p["name"] for p in piezas], ["1.png", "2.mp4", "10.jpg"])

    def test_archivo_suelto(self):
        self.assertEqual(len(self.revisar([video_meta("v.mp4", 20)], carpeta=False, file_id="id-v.mp4")), 1)

    def test_errores(self):
        casos = [([video_meta("largo.mp4", 75)], ("Instagram", "Facebook"), "largo.mp4"),
                 ([meta(f"{i}.jpg") for i in range(11)], ("Instagram",), "máximo es 10"),
                 ([meta("a.psd", "image/vnd.adobe.photoshop")], ("Instagram",), "no admitido"),
                 ([meta("notas.txt", "text/plain")], ("Instagram",), "ni videos")]
        for archivos, redes, texto in casos:
            with self.assertRaises(ErrorValidacion) as e:
                self.revisar(archivos, redes)
            self.assertIn(texto, str(e.exception))

    def test_video_de_75s_solo_sirve_en_facebook(self):
        self.assertTrue(self.revisar([video_meta("largo.mp4", 75)], redes=("Facebook",)))


class InstagramStories(unittest.TestCase):
    def ig(self, reglas):
        self.s = SesionFalsa(reglas)
        return Instagram("TOK", "IG1", "v24.0", sesion=self.s, dormir=lambda s: None)

    def test_foto_y_video(self):
        ig = self.ig({
            ("POST", "/IG1/media_publish"): [Resp(200, {"id": "M1"}), Resp(200, {"id": "M2"})],
            ("POST", "/IG1/media"): [Resp(200, {"id": "C1"}), Resp(200, {"id": "C2", "uri": "https://rupload/C2"})],
            ("POST", "rupload"): Resp(200, {}),
            ("GET", "/C"): Resp(200, {"status_code": "FINISHED"}),
            ("GET", "/M1"): Resp(200, {"permalink": "https://instagram.com/stories/bambu/M1"}),
        })
        ids, orden = [], []
        sec = Secuencia([foto("1.jpg", 1080, 1920, url="https://notion/1"), archivo(20)])
        r = ig.publicar_story(T, sec, esperar=lambda: orden.append(len(self.s.llamadas)), guardar_id=ids.append)
        self.assertEqual((r.estado, r.id_publicacion), ("Publicado", "M1,M2"))
        self.assertEqual(r.url, "https://instagram.com/stories/bambu/M1")
        self.assertEqual(ids[-1], "story:C1,C2")
        posts = [k["data"] for m, u, k in self.s.llamadas if m == "POST" and u.endswith("/media")]
        self.assertEqual(posts[0], {"media_type": "STORIES", "image_url": "https://notion/1"})
        self.assertEqual(posts[1], {"media_type": "STORIES", "upload_type": "resumable"})
        self.assertNotIn("caption", posts[0])
        publicar = [i for i, (m, u, k) in enumerate(self.s.llamadas) if u.endswith("/media_publish")]
        self.assertEqual(len(publicar), 2)
        self.assertEqual(orden[0], publicar[0])              # se espera la hora justo antes de publicar la primera
        self.assertEqual([self.s.llamadas[i][2]["data"]["creation_id"] for i in publicar], ["C1", "C2"])

    def test_recuperar(self):
        t = T.__class__(**{**T.__dict__, "id_subida": "story:C1,C2"})
        casos = {("FINISHED", "FINISHED"): REINTENTAR, ("PUBLISHED", "PUBLISHED"): "Publicado"}
        for estados, esperado in casos.items():
            ig = self.ig({("GET", "/C1"): Resp(200, {"status_code": estados[0]}),
                          ("GET", "/C2"): Resp(200, {"status_code": estados[1]})})
            r = ig.recuperar(t)
            self.assertEqual(getattr(r, "estado", r), esperado)
        ig = self.ig({("GET", "/C1"): Resp(200, {"status_code": "PUBLISHED"}),
                      ("GET", "/C2"): Resp(200, {"status_code": "FINISHED"})})
        with self.assertRaises(ResultadoIncierto):
            ig.recuperar(t)


class FacebookStories(unittest.TestCase):
    def fb(self, reglas):
        self.s = SesionFalsa(reglas)
        fb = Facebook("SYS", "PAG", "v24.0", sesion=self.s)
        fb._token_pagina = "PT"
        return fb

    def test_foto_y_video(self):
        fb = self.fb({("POST", "/PAG/photos"): Resp(200, {"id": "F1"}),
                      ("POST", "/PAG/photo_stories"): Resp(200, {"success": True, "post_id": "S1"}),
                      ("POST", "/PAG/video_stories"): [Resp(200, {"video_id": "V2", "upload_url": "https://rupload/V2"}),
                                                       Resp(200, {"success": True, "post_id": "S2"})],
                      ("POST", "rupload"): Resp(200, {})})
        ids, orden = [], []
        sec = Secuencia([foto("1.jpg", 1080, 1920), archivo(80)])
        r = fb.publicar_story(T, sec, esperar=lambda: orden.append(list(ids)), guardar_id=ids.append)
        self.assertEqual((r.estado, r.id_publicacion), ("Publicado", "S1,S2"))
        self.assertEqual(orden, [["story:foto-F1,video-V2"]])  # todo subido y guardado antes de esperar la hora
        self.assertEqual(ids[-1], "story:foto-F1,video-V2|publicando")
        self.assertEqual(self.s.llamadas[0][2]["data"], {"published": "false"})
        self.assertEqual(self.s.llamadas[3][2]["data"], {"photo_id": "F1"})
        self.assertEqual(self.s.llamadas[4][2]["data"], {"upload_phase": "finish", "video_id": "V2"})

    def test_recuperar(self):
        fb = self.fb({})
        antes = T.__class__(**{**T.__dict__, "id_subida": "story:foto-F1"})
        self.assertEqual(fb.recuperar(antes), REINTENTAR)
        durante = T.__class__(**{**T.__dict__, "id_subida": "story:foto-F1|publicando"})
        with self.assertRaises(ResultadoIncierto):
            fb.recuperar(durante)

    def test_red_sin_stories(self):
        with self.assertRaises(ErrorPermanente):
            Adaptador().publicar_story(T, Secuencia([]), lambda: None, lambda x: None)


# ---------------------------------------------------------------- motor de punta a punta

class MediosDeStories:
    def __init__(self):
        self.revisadas = 0

    def story(self, c):
        self.revisadas += 1
        return [meta("1.jpg"), video_meta("2.mp4", 15)]

    def fotos(self, c):
        raise AssertionError("un contenido de Stories no se trata como carrusel")

    def secuencia(self, c, red):
        return Secuencia([foto("1.jpg", 1080, 1920), archivo(15)])


class RedDeStories(Adaptador):
    def __init__(self, por_url=False, nativo=False):
        self.fotos_por_url, self.programa_nativo = por_url, nativo
        self.llamadas = []

    def publicar_story(self, t, secuencia, esperar, guardar_id):
        guardar_id("story:A,B")
        esperar()
        self.llamadas.append(("publicar_story", [getattr(p, "url", None) for p in secuencia.piezas]))
        return Resultado("Publicado", "S1,S2", "https://red/s", T0)

    def programar(self, *a):
        raise AssertionError("las Stories nunca se programan en la plataforma")


class MotorStories(Base):
    def setUp(self):
        super().setUp()
        self.n = NotionConArchivos()
        self.redes = {"Instagram": RedDeStories(por_url=True), "Facebook": RedDeStories(nativo=True)}
        self.medios = MediosDeStories()

    def correr(self):
        def dormir(s):
            self.reloj.t += timedelta(seconds=s)
        return Motor(self.cfg, self.n, lambda cuenta, red: self.redes[red], self.medios, reloj=self.reloj,
                     dormir=dormir).ejecutar()

    def story(self, redes=("Instagram", "Facebook"), **kw):
        return self.contenido(**STORY, **{C.URL: N.enlace(CARPETA),
                                          C.REDES: {"multi_select": [{"name": r} for r in redes]}}, **kw)

    def test_se_publican_a_la_hora_en_las_dos_redes(self):
        cid = self.story()
        self.correr()
        self.assertEqual(self.estado(cid), "Programado")
        filas = self.filas()
        self.assertEqual({r: self.estado(f["id"]) for r, f in filas.items()},
                         {"Instagram": "En cola", "Facebook": "En cola"})   # Facebook no se programó en la plataforma
        self.reloj.t = T0 - timedelta(minutes=10)
        self.correr()
        self.assertEqual(self.estado(cid), "Publicado")
        self.assertGreaterEqual(self.reloj.t, T0)                            # esperó la hora exacta
        ig = self.redes["Instagram"].llamadas[0][1]
        self.assertEqual(ig, ["https://notion/1.jpg?firma=0", None])        # solo la foto se aloja en Notion
        self.assertEqual(self.redes["Facebook"].llamadas[0][1], [None, None])
        self.assertNotIn(filas["Facebook"]["id"], self.n.bloques)
        fila = self.n.paginas[filas["Instagram"]["id"]]
        self.assertEqual(N.leer(fila, P.ID_PUB), "S1,S2")
        self.assertIn("copy y los hashtags no salen", N.leer(fila, P.ERROR))

    def test_sin_copy_no_hay_aviso(self):
        self.story(("Instagram",), **{C.COPY: N.texto(""), C.HASHTAGS: N.texto("")})
        self.correr()
        self.reloj.t = T0
        self.correr()
        self.assertEqual(N.leer(self.n.paginas[self.filas()["Instagram"]["id"]], P.ERROR), "")

    def test_tiktok_no_tiene_stories(self):
        self.cfg.cuentas["Bambú"]["TikTok"] = Destino("TikTok", "nativo", {"secreto": "t"})
        cid = self.story(("Instagram", "TikTok"))
        self.correr()
        self.assertEqual(self.estado(cid), "Error")
        self.assertIn("TikTok no permite publicar Stories", N.leer(self.n.paginas[cid], C.ERROR))
        self.assertEqual(self.filas(), {})

    def test_se_valida_al_marcar_listo(self):
        self.story()
        self.correr()
        self.assertEqual(self.medios.revisadas, 1)


if __name__ == "__main__":
    unittest.main()
