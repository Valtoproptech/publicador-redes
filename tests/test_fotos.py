"""Fotos y carruseles: reglas, imágenes reales (Pillow), adaptadores y motor con dobles en memoria."""
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from PIL import Image

from publicador import notion as N
from publicador.config import Config, Destino
from publicador.errores import ErrorPermanente, ErrorTransitorio, ErrorValidacion, ResultadoIncierto
from publicador.esquema import C, P
from publicador.imagenes import Album, Foto, analizar_foto, preparar_foto
from publicador.motor import Medios, Motor
from publicador.redes.base import Adaptador, Resultado
from publicador.redes.facebook import Facebook
from publicador.redes.instagram import Instagram
from publicador.reglas import InfoFoto, diagnosticar_foto, enlace_drive, orden_natural, proporcion_fuera
from tests.test_motor import T0, Base, NotionFalso, Reloj
from tests.test_redes import T, Resp, SesionFalsa

CARPETA = "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz?usp=sharing"


class Reglas(unittest.TestCase):
    def test_enlaces(self):
        self.assertEqual(enlace_drive(CARPETA), ("1AbCdEfGhIjKlMnOpQrStUvWxYz", True))
        self.assertEqual(enlace_drive("https://drive.google.com/drive/u/1/folders/1AbCdEfGhIjKlMn"), ("1AbCdEfGhIjKlMn", True))
        self.assertEqual(enlace_drive("https://drive.google.com/file/d/1AbCdEfGhIjKlMnOp/view"), ("1AbCdEfGhIjKlMnOp", False))
        with self.assertRaises(ErrorValidacion):
            enlace_drive("https://youtube.com/watch?v=x")

    def test_orden_natural(self):
        self.assertEqual(sorted(["10.jpg", "2.jpg", "1.jpg", "Portada.png"], key=orden_natural),
                         ["1.jpg", "2.jpg", "10.jpg", "Portada.png"])

    def test_proporcion(self):
        self.assertIsNone(proporcion_fuera(1080, 1350, "Instagram"))     # 4:5
        self.assertIsNone(proporcion_fuera(1080, 1080, "Instagram"))
        self.assertIsNone(proporcion_fuera(1910, 1000, "Instagram"))     # 1.91:1
        self.assertIn("4:5", proporcion_fuera(1080, 1920, "Instagram"))  # historia 9:16
        self.assertIsNone(proporcion_fuera(1080, 1920, "Facebook"))      # Facebook no limita

    def test_diagnostico(self):
        self.assertTrue(diagnosticar_foto(InfoFoto("JPEG", 1080, 1350, 900_000), "Instagram").ok)
        self.assertEqual(diagnosticar_foto(InfoFoto("PNG", 1080, 1350, 900_000), "Instagram").arreglable, ["formato PNG"])
        self.assertTrue(diagnosticar_foto(InfoFoto("PNG", 1080, 1350, 900_000), "Facebook").ok)
        self.assertIn("rotación EXIF", diagnosticar_foto(InfoFoto("JPEG", 1080, 1350, 9, 6), "Instagram").arreglable)


class Imagenes(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def imagen(self, nombre, ancho, alto, formato="JPEG", modo="RGB", ruido=False, **kw):
        ruta = self.tmp / nombre
        if ruido:
            im = Image.effect_noise((ancho, alto), 120).convert(modo)
        else:
            im = Image.new(modo, (ancho, alto), (200, 80, 40, 255)[:len(modo)])
        im.save(ruta, formato, **kw)
        return ruta

    def test_jpeg_compatible_queda_intacto(self):
        ruta = self.imagen("a.jpg", 1080, 1350)
        antes = ruta.read_bytes()
        for red in ("Instagram", "Facebook"):
            f = preparar_foto(ruta, "a.jpg", red, self.tmp)
            self.assertEqual(f.ruta, ruta)
            self.assertTrue(f.detalle.startswith("Original intacto"))
        self.assertEqual(ruta.read_bytes(), antes)

    def test_png_se_convierte_solo_para_instagram(self):
        ruta = self.imagen("b.png", 1080, 1080, "PNG", "RGBA")
        ig = preparar_foto(ruta, "b.png", "Instagram", self.tmp)
        self.assertEqual(ig.info.formato, "JPEG")
        self.assertIn("Convertida a JPEG", ig.detalle)
        self.assertEqual(preparar_foto(ruta, "b.png", "Facebook", self.tmp).ruta, ruta)

    def test_historia_9_16_no_sirve_en_instagram(self):
        ruta = self.imagen("c.jpg", 1080, 1920)
        with self.assertRaises(ErrorValidacion) as e:
            preparar_foto(ruta, "c.jpg", "Instagram", self.tmp)
        self.assertIn("c.jpg", str(e.exception))
        self.assertEqual(preparar_foto(ruta, "c.jpg", "Facebook", self.tmp).ruta, ruta)

    def test_muy_pesada_se_reduce_a_1440(self):
        ruta = self.imagen("d.jpg", 4000, 5000, ruido=True, quality=100)
        self.assertGreater(ruta.stat().st_size, 5 * 1024 * 1024)
        f = preparar_foto(ruta, "d.jpg", "Instagram", self.tmp)
        self.assertEqual((f.info.ancho, f.info.alto), (1440, 1800))
        self.assertLessEqual(f.ruta.stat().st_size, 5 * 1024 * 1024)

    def test_rotacion_exif_se_aplica(self):
        exif = Image.Exif()
        exif[274] = 6   # girar 90°: el archivo es 1350x1080 pero se ve 1080x1350
        ruta = self.imagen("e.jpg", 1350, 1080, exif=exif.tobytes())
        self.assertEqual((analizar_foto(ruta).ancho, analizar_foto(ruta).alto), (1080, 1350))
        f = preparar_foto(ruta, "e.jpg", "Instagram", self.tmp)
        with Image.open(f.ruta) as im:
            self.assertEqual(im.size, (1080, 1350))


def foto(nombre="1.jpg", ancho=1080, alto=1350, url=None):
    ruta = Path(tempfile.mkdtemp()) / nombre
    ruta.write_bytes(b"jpg")
    return Foto(ruta, nombre, InfoFoto("JPEG", ancho, alto, 3), "Original intacto", "md5" + nombre, url)


class InstagramFotos(unittest.TestCase):
    def ig(self, reglas):
        self.s = SesionFalsa(reglas)
        return Instagram("TOK", "IG1", "v24.0", sesion=self.s, dormir=lambda s: None)

    def test_carrusel(self):
        ig = self.ig({
            ("POST", "/IG1/media_publish"): Resp(200, {"id": "M1"}),
            ("POST", "/IG1/media"): [Resp(200, {"id": "H1"}), Resp(200, {"id": "H2"}), Resp(200, {"id": "CAR"})],
            ("GET", "/H"): Resp(200, {"status_code": "FINISHED"}),
            ("GET", "/CAR"): Resp(200, {"status_code": "FINISHED"}),
            ("GET", "/M1"): Resp(200, {"permalink": "https://instagram.com/p/abc"}),
        })
        ids, orden = [], []
        album = Album([foto("1.jpg", url="https://notion/1"), foto("2.jpg", url="https://notion/2")])
        r = ig.publicar_fotos(T, album, esperar=lambda: orden.append("esperar"), guardar_id=ids.append)
        self.assertEqual((r.estado, r.id_publicacion, r.url), ("Publicado", "M1", "https://instagram.com/p/abc"))
        self.assertEqual(ids, ["CAR"])
        self.assertEqual(orden, ["esperar"])
        posts = [k["data"] for m, u, k in self.s.llamadas if m == "POST" and u.endswith("/media")]
        self.assertEqual(posts[0], {"image_url": "https://notion/1", "is_carousel_item": "true"})
        self.assertEqual(posts[2]["media_type"], "CAROUSEL")
        self.assertEqual(posts[2]["children"], "H1,H2")
        self.assertEqual(posts[2]["caption"], T.texto)
        self.assertEqual(posts[2]["collaborators"], '["valto"]')
        self.assertEqual(r.nota, "")

    def test_foto_suelta(self):
        ig = self.ig({
            ("POST", "/IG1/media_publish"): Resp(200, {"id": "M1"}),
            ("POST", "/IG1/media"): Resp(200, {"id": "C1"}),
            ("GET", "/C1"): Resp(200, {"status_code": "FINISHED"}),
            ("GET", "/M1"): Resp(200, {"permalink": "https://instagram.com/p/x"}),
        })
        r = ig.publicar_fotos(T, Album([foto(url="https://notion/1")]), esperar=lambda: None, guardar_id=lambda x: None)
        datos = self.s.llamadas[0][2]["data"]
        self.assertEqual(datos["image_url"], "https://notion/1")
        self.assertNotIn("media_type", datos)
        self.assertEqual(r.estado, "Publicado")

    def test_proporciones_distintas_avisa(self):
        ig = self.ig({
            ("POST", "/IG1/media_publish"): Resp(200, {"id": "M1"}),
            ("POST", "/IG1/media"): [Resp(200, {"id": "H1"}), Resp(200, {"id": "H2"}), Resp(200, {"id": "CAR"})],
            ("GET", "/"): Resp(200, {"status_code": "FINISHED"}),
        })
        album = Album([foto("1.jpg", 1080, 1350, "u1"), foto("2.jpg", 1080, 1080, "u2")])
        r = ig.publicar_fotos(T, album, esperar=lambda: None, guardar_id=lambda x: None)
        self.assertIn("recorta", r.nota)


class FacebookFotos(unittest.TestCase):
    def fb(self, reglas):
        self.s = SesionFalsa({("GET", "/PAG?"): Resp(200, {"access_token": "PT"})} | reglas)
        fb = Facebook("SYS", "PAG", "v24.0", sesion=self.s)
        fb._token_pagina = "PT"
        return fb

    def test_programa_post_de_fotos(self):
        fb = self.fb({("POST", "/PAG/photos"): [Resp(200, {"id": "F1"}), Resp(200, {"id": "F2"})],
                      ("POST", "/PAG/feed"): Resp(200, {"id": "PAG_99"})})
        ids = []
        r = fb.programar_fotos(T, Album([foto("1.jpg"), foto("2.jpg")]), ids.append)
        self.assertEqual((r.estado, r.id_publicacion), ("Programado", "PAG_99"))
        self.assertEqual(ids, ["fotos:F1,F2"])
        subida = self.s.llamadas[0][2]["data"]
        self.assertEqual(subida, {"published": "false", "temporary": "true"})
        feed = self.s.llamadas[2][2]["data"]
        self.assertEqual(json.loads(feed["attached_media[1]"]), {"media_fbid": "F2"})
        self.assertEqual(feed["scheduled_publish_time"], int(T.cuando.timestamp()))
        self.assertEqual(feed["published"], "false")
        self.assertEqual(feed["message"], T.texto)

    def test_publica_a_la_hora(self):
        fb = self.fb({("POST", "/PAG/photos"): Resp(200, {"id": "F1"}),
                      ("POST", "/PAG/feed"): Resp(200, {"id": "PAG_99"})})
        orden = []
        r = fb.publicar_fotos(T, Album([foto()]), esperar=lambda: orden.append("esperar"), guardar_id=lambda x: None)
        self.assertEqual(r.estado, "Publicado")
        self.assertEqual(self.s.llamadas[0][2]["data"], {"published": "false"})
        self.assertNotIn("scheduled_publish_time", self.s.llamadas[1][2]["data"])
        self.assertEqual(orden, ["esperar"])

    def test_verificar_post(self):
        fb = self.fb({("GET", "/PAG_99"): Resp(200, {"is_published": True, "permalink_url": "https://fb/p"})})
        t = T.__class__(**{**T.__dict__, "id_publicacion": "PAG_99"})
        r = fb.verificar(t)
        self.assertEqual((r.estado, r.url), ("Publicado", "https://fb/p"))

    def test_corte_al_crear_post_va_a_revision(self):
        fb = self.fb({})
        t = T.__class__(**{**T.__dict__, "id_subida": "fotos:F1,F2"})
        with self.assertRaises(ResultadoIncierto):
            fb.recuperar(t)

    def test_red_sin_fotos(self):
        with self.assertRaises(ErrorPermanente):
            Adaptador().publicar_fotos(T, Album([]), lambda: None, lambda x: None)


# ---------------------------------------------------------------- Medios.fotos con un Drive falso

class DriveFalso:
    def __init__(self, archivos):
        self.archivos = archivos

    def listar(self, carpeta_id):
        return self.archivos

    def metadatos(self, file_id):
        return next(a for a in self.archivos if a["id"] == file_id)


def meta(nombre, mime="image/jpeg", ancho=1080, alto=1350, rotacion=0):
    return {"id": "id-" + nombre, "name": nombre, "mimeType": mime,
            "imageMediaMetadata": {"width": ancho, "height": alto, "rotation": rotacion}}


class MediosFotos(unittest.TestCase):
    def revisar(self, archivos, redes=("Instagram",), carpeta=True, file_id="x"):
        c = type("C", (), {"video_id": file_id, "carpeta": carpeta, "redes": list(redes)})()
        return Medios(DriveFalso(archivos), Path(tempfile.mkdtemp())).fotos(c)

    def test_orden_y_filtrado(self):
        fotos = self.revisar([meta("10.jpg"), meta("2.png", "image/png"), meta("notas.txt", "text/plain")])
        self.assertEqual([f["name"] for f in fotos], ["2.png", "10.jpg"])

    def test_errores(self):
        casos = [([meta("a.mp4", "video/mp4"), meta("b.jpg")], "video"),
                 ([meta(f"{i}.jpg") for i in range(11)], "máximo 10"),
                 ([meta("notas.txt", "text/plain")], "no tiene fotos"),
                 ([meta("a.psd", "image/vnd.adobe.photoshop")], "no admitido"),
                 ([meta("historia.jpg", alto=1920)], "historia.jpg")]
        for archivos, texto in casos:
            with self.assertRaises(ErrorValidacion) as e:
                self.revisar(archivos)
            self.assertIn(texto, str(e.exception))

    def test_rotacion_de_drive(self):
        self.assertTrue(self.revisar([meta("a.jpg", ancho=1350, alto=1080, rotacion=1)]))  # se ve 1080x1350

    def test_proporcion_solo_importa_en_instagram(self):
        self.assertTrue(self.revisar([meta("h.jpg", alto=1920)], redes=("Facebook",)))

    def test_archivo_suelto(self):
        self.assertIsNone(self.revisar([meta("v.mp4", "video/mp4")], carpeta=False, file_id="id-v.mp4"))
        self.assertEqual(len(self.revisar([meta("f.jpg")], carpeta=False, file_id="id-f.jpg")), 1)
        with self.assertRaises(ErrorValidacion):
            self.revisar([meta("f.jpg")], redes=("Instagram", "TikTok"), carpeta=False, file_id="id-f.jpg")


# ---------------------------------------------------------------- motor de punta a punta

class NotionConArchivos(NotionFalso):
    def __init__(self):
        super().__init__()
        self.bloques: dict[str, list] = {}

    def hijos(self, pid):
        return self.bloques.get(pid, [])

    def agregar_imagen(self, pid, ruta, nombre, leyenda):
        url = f"https://notion/{nombre}?firma={len(self.bloques.get(pid, []))}"
        self.bloques.setdefault(pid, []).append({"type": "image", "image": {
            "type": "file", "file": {"url": url}, "caption": [{"plain_text": leyenda}]}})
        return url


class MediosDeFotos:
    def __init__(self, error=None):
        self.error = error
        self.albumes = 0

    def fotos(self, c):
        if self.error:
            raise self.error
        return [meta("1.jpg"), meta("2.jpg")] if c.carpeta else None

    def album(self, c, red):
        self.albumes += 1
        return Album([foto("1.jpg"), foto("2.jpg")])


class RedDeFotos(Adaptador):
    def __init__(self, por_url=False, nativo=False):
        self.fotos_por_url, self.programa_nativo = por_url, nativo
        self.llamadas, self.fallo = [], None

    def publicar_fotos(self, t, album, esperar, guardar_id):
        guardar_id("CAR")
        esperar()
        self.llamadas.append(("publicar_fotos", [f.url for f in album.fotos]))
        if self.fallo:
            raise self.fallo
        return Resultado("Publicado", "M1", "https://ig/p/1", T0)

    def programar_fotos(self, t, album, guardar_id):
        self.llamadas.append(("programar_fotos", t.cuando))
        return Resultado("Programado", "PAG_1", "https://fb/PAG_1")

    def verificar(self, t):
        return None


class MotorFotos(Base):
    def setUp(self):
        super().setUp()
        self.n = NotionConArchivos()
        self.redes = {"Instagram": RedDeFotos(por_url=True), "Facebook": RedDeFotos(nativo=True)}
        self.medios = MediosDeFotos()

    def correr(self):
        def dormir(s):
            self.reloj.t += timedelta(seconds=s)
        return Motor(self.cfg, self.n, lambda cuenta, red: self.redes[red], self.medios, reloj=self.reloj,
                     dormir=dormir).ejecutar()

    def carrusel(self, redes=("Instagram",)):
        return self.contenido(**{C.URL: N.enlace(CARPETA), C.REDES: {"multi_select": [{"name": r} for r in redes]}})

    def test_instagram_recibe_urls_de_notion(self):
        cid = self.carrusel()
        self.correr()
        fila = self.filas()["Instagram"]["id"]
        self.reloj.t = T0 - timedelta(minutes=4)
        self.correr()
        self.assertEqual(self.estado(fila), "Publicado")
        self.assertEqual(self.estado(cid), "Publicado")
        urls = self.redes["Instagram"].llamadas[0][1]
        self.assertEqual(urls, ["https://notion/1.jpg?firma=0", "https://notion/2.jpg?firma=1"])
        self.assertEqual(len(self.n.bloques[fila]), 2)             # las fotos quedan visibles en la fila
        self.assertIn("1. 1.jpg", N.leer(self.n.paginas[fila], P.DETALLE))

    def test_reintento_reutiliza_las_fotos_ya_subidas(self):
        self.carrusel()
        self.correr()
        fila = self.filas()["Instagram"]["id"]
        self.redes["Instagram"].fallo = ErrorTransitorio("5xx")
        self.reloj.t = T0
        self.correr()
        self.assertEqual(self.estado(fila), "En cola")
        self.redes["Instagram"].fallo = None
        self.correr()
        self.assertEqual(self.estado(fila), "Publicado")
        self.assertEqual(len(self.n.bloques[fila]), 2)             # no se duplicaron

    def test_facebook_se_programa_sin_alojar(self):
        self.carrusel(("Facebook",))
        self.correr()
        fila = self.filas()["Facebook"]["id"]
        self.assertEqual(self.estado(fila), "Programado")
        self.assertEqual(self.redes["Facebook"].llamadas[0][0], "programar_fotos")
        self.assertNotIn(fila, self.n.bloques)

    def test_tiktok_con_carpeta_es_error_de_datos(self):
        self.cfg.cuentas["Bambú"]["TikTok"] = Destino("TikTok", "nativo", {"secreto": "t"})
        cid = self.carrusel(("Instagram", "TikTok"))
        self.correr()
        self.assertEqual(self.estado(cid), "Error")
        self.assertIn("TikTok no admite fotos", N.leer(self.n.paginas[cid], C.ERROR))
        self.assertEqual(self.filas(), {})

    def test_problema_de_fotos_se_avisa_al_marcar_listo(self):
        self.medios.error = ErrorValidacion("La foto 'h.jpg' mide 1080x1920")
        cid = self.carrusel()
        self.correr()
        self.assertEqual(self.estado(cid), "Error")
        self.assertEqual(self.filas(), {})

    def test_drive_caido_deja_el_contenido_en_listo(self):
        self.medios.error = ErrorTransitorio("Drive 503")
        cid = self.carrusel()
        self.correr()
        self.assertEqual(self.estado(cid), "Listo para publicar")
        self.medios.error = None
        self.correr()
        self.assertEqual(self.estado(cid), "Programado")

    def test_video_sigue_igual(self):
        self.cfg = Config("CONT", "PUBS", cuentas=self.cfg.cuentas)
        from tests.test_motor import MediosFalsos, RedFalsa
        self.medios, self.redes["Instagram"] = MediosFalsos(), RedFalsa()
        self.contenido()
        self.correr()
        self.reloj.t = T0
        self.correr()
        self.assertEqual(self.redes["Instagram"].llamadas[0][0], "publicar")
        self.assertEqual(self.n.bloques, {})


if __name__ == "__main__":
    unittest.main()
