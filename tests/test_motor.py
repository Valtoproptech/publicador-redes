"""Pruebas de extremo a extremo del motor con Notion y redes simuladas en memoria."""
import copy
import itertools
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from publicador import notion as N
from publicador.config import Config, Destino
from publicador.errores import ErrorPermanente, ErrorTransitorio, ResultadoIncierto
from publicador.esquema import C, P
from publicador.media import ArchivoPreparado
from publicador.motor import Motor
from publicador.redes.base import REINTENTAR, Adaptador, Resultado
from publicador.reglas import InfoVideo

T0 = datetime(2026, 10, 6, 23, 0, tzinfo=timezone.utc)   # 18:00 Lima
URL = "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/view"


class NotionFalso:
    def __init__(self):
        self.paginas: dict[str, dict] = {}
        self._ids = (f"p{i}" for i in itertools.count())

    def crear(self, base, props):
        pid = next(self._ids)
        self.paginas[pid] = {"id": pid, "base": base, "properties": copy.deepcopy(props)}
        return self.paginas[pid]

    def actualizar(self, pid, props):
        self.paginas[pid]["properties"].update(copy.deepcopy(props))

    def pagina(self, pid):
        return copy.deepcopy(self.paginas[pid])

    def _cumple(self, pag, f):
        if "or" in f:
            return any(self._cumple(pag, x) for x in f["or"])
        valor = N.leer(pag, f["property"])
        if "select" in f:
            return valor == f["select"]["equals"]
        if "relation" in f:
            return f["relation"]["contains"] in (valor or [])
        raise NotImplementedError(f)

    def consultar(self, base, filtro=None):
        return [copy.deepcopy(p) for p in self.paginas.values()
                if p["base"] == base and (filtro is None or self._cumple(p, filtro))]


class RedFalsa(Adaptador):
    def __init__(self, nativo=False):
        self.programa_nativo = nativo
        self.llamadas = []
        self.fallo = None          # excepción a lanzar en publicar/programar
        self.al_verificar = None   # Resultado a devolver en verificar
        self.al_recuperar = REINTENTAR

    def programar(self, t, video, portada, guardar_id):
        self.llamadas.append(("programar", t.cuando))
        guardar_id("subida-1")
        if self.fallo:
            raise self.fallo
        return Resultado("Programado", "vid-1", "https://red/vid-1")

    def publicar(self, t, video, portada, esperar, guardar_id):
        guardar_id("subida-1")
        esperar()
        self.llamadas.append(("publicar", t.texto))
        if self.fallo:
            raise self.fallo
        return Resultado("Publicado", "media-1", "https://red/media-1", T0)

    def verificar(self, t):
        self.llamadas.append(("verificar", t.id_publicacion))
        return self.al_verificar

    def recuperar(self, t):
        self.llamadas.append(("recuperar", t.id_subida))
        return self.al_recuperar

    def reprogramar(self, t, cuando):
        self.llamadas.append(("reprogramar", cuando))


class MediosFalsos:
    def __init__(self):
        self.bajadas = 0

    def video(self, file_id, red):
        self.bajadas += 1
        info = InfoVideo("mov,mp4", "h264", 1080, 1920, 30, 45, 16_000_000, 90_000_000, "aac", 48000)
        return ArchivoPreparado(Path("/tmp/x.mp4"), info, "Original intacto")

    def portada(self, url):
        return Path("/tmp/p.jpg")

    def fotos(self, c):
        return None   # todos los contenidos de estas pruebas son videos


class Reloj:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class Base(unittest.TestCase):
    def setUp(self):
        self.n = NotionFalso()
        self.cfg = Config("CONT", "PUBS", cuentas={"Bambú": {
            "Instagram": Destino("Instagram", "nativo", {"ig_user_id": "1"}),
            "Facebook": Destino("Facebook", "nativo", {"page_id": "2"}),
            "TikTok": Destino("TikTok", "uploadpost", {"perfil": "bambu"}),
        }})
        self.redes = {"Instagram": RedFalsa(), "Facebook": RedFalsa(nativo=True), "TikTok": RedFalsa()}
        self.reloj = Reloj(T0 - timedelta(days=2))
        self.dormido = []

    def contenido(self, **kw):
        props = {C.TITULO: N.titulo("Depa demo"), C.ESTADO: N.opcion("Listo para publicar"),
                 C.CUENTA: N.opcion("Bambú"), C.REDES: {"multi_select": [{"name": "Instagram"}]},
                 C.FECHA: {"date": {"start": "2026-10-06T18:00:00.000-05:00"}}, C.URL: N.enlace(URL),
                 C.COPY: N.texto("Tu señal para alquilar"), C.HASHTAGS: N.texto("bambu lima")}
        props.update(kw)
        return self.n.crear("CONT", props)["id"]

    def correr(self):
        def dormir(s):
            self.dormido.append(s)
            self.reloj.t += timedelta(seconds=s)
        m = Motor(self.cfg, self.n, lambda cuenta, red: self.redes[red], MediosFalsos(), reloj=self.reloj,
                  dormir=dormir)
        return m.ejecutar()

    def filas(self, cid=None):
        return {N.leer(p, P.RED): p for p in self.n.consultar("PUBS")
                if cid is None or cid in N.leer(p, P.CONTENIDO)}

    def estado(self, pid):
        return N.leer(self.n.paginas[pid], "Estado")


class FlujoCompleto(Base):
    def test_instagram_de_punta_a_punta(self):
        cid = self.contenido()
        self.correr()
        fila = self.filas()["Instagram"]
        self.assertEqual(self.estado(fila["id"]), "En cola")
        self.assertEqual(self.estado(cid), "Programado")

        self.reloj.t = T0 - timedelta(minutes=4)
        self.correr()
        fila = self.n.paginas[fila["id"]]
        self.assertEqual(N.leer(fila, P.ESTADO), "Publicado")
        self.assertEqual(N.leer(fila, P.ID_PUB), "media-1")
        self.assertEqual(N.leer(fila, P.URL_PUB), "https://red/media-1")
        self.assertEqual(self.redes["Instagram"].llamadas, [("publicar", "Tu señal para alquilar\n\n#bambu #lima")])
        self.assertEqual(self.reloj.t, T0)               # esperó hasta la hora exacta
        self.assertEqual(self.estado(cid), "Publicado")
        self.assertIn("Instagram ✅", N.leer(self.n.paginas[cid], C.RESULTADO))

    def test_no_publica_dos_veces_aunque_se_reactive(self):
        cid = self.contenido()
        self.correr()
        self.reloj.t = T0
        self.correr()
        self.n.actualizar(cid, {C.ESTADO: N.opcion("Listo para publicar")})
        self.correr()
        self.correr()
        self.assertEqual(len(self.redes["Instagram"].llamadas), 1)
        self.assertEqual(len(self.filas()), 1)

    def test_facebook_se_programa_y_luego_se_verifica(self):
        cid = self.contenido(**{C.REDES: {"multi_select": [{"name": "Facebook"}]}})
        self.correr()
        fila = self.filas()["Facebook"]["id"]
        self.assertEqual(self.estado(fila), "Programado")
        self.assertEqual(self.redes["Facebook"].llamadas[0][0], "programar")

        self.reloj.t = T0 + timedelta(minutes=10)
        self.redes["Facebook"].al_verificar = Resultado("Publicado", "vid-1", "https://fb/reel", T0)
        self.correr()
        self.assertEqual(self.estado(fila), "Publicado")
        self.assertEqual(self.estado(cid), "Publicado")

    def test_tres_redes_una_falla(self):
        cid = self.contenido(**{C.REDES: {"multi_select": [{"name": r} for r in ("Instagram", "Facebook", "TikTok")]}})
        self.redes["TikTok"].fallo = ErrorPermanente("TikTok rechazó el video")
        self.correr()
        self.reloj.t = T0
        self.correr()
        filas = self.filas()
        self.assertEqual(self.estado(filas["Instagram"]["id"]), "Publicado")
        self.assertEqual(self.estado(filas["TikTok"]["id"]), "Error")
        self.assertEqual(self.estado(cid), "Requiere atención")
        self.assertIn("TikTok: TikTok rechazó el video", N.leer(self.n.paginas[cid], C.ERROR))

        # el equipo corrige y reactiva: solo se reintenta TikTok
        self.redes["TikTok"].fallo = None
        self.n.actualizar(cid, {C.ESTADO: N.opcion("Listo para publicar")})
        self.correr()
        self.assertEqual(len(self.redes["Instagram"].llamadas), 1)
        self.assertEqual(self.estado(filas["TikTok"]["id"]), "Publicado")


class Errores(Base):
    def test_validacion(self):
        cid = self.contenido(**{C.FECHA: {"date": {"start": "2026-10-06"}}})
        self.correr()
        self.assertEqual(self.estado(cid), "Error")
        self.assertIn("hora", N.leer(self.n.paginas[cid], C.ERROR))
        self.assertEqual(self.filas(), {})

    def test_red_no_configurada(self):
        cid = self.contenido(**{C.REDES: {"multi_select": [{"name": "YouTube"}]}})
        self.correr()
        self.assertIn("no tiene YouTube", N.leer(self.n.paginas[cid], C.ERROR))

    def test_transitorio_reintenta_y_luego_error(self):
        self.contenido()
        self.correr()
        self.redes["Instagram"].fallo = ErrorTransitorio("5xx")
        fila = self.filas()["Instagram"]["id"]
        self.reloj.t = T0
        self.correr()
        self.assertEqual(self.estado(fila), "En cola")
        self.assertEqual(N.leer(self.n.paginas[fila], P.INTENTOS), 1)
        self.correr()
        self.correr()
        self.assertEqual(self.estado(fila), "Error")

    def test_incierto_no_se_reintenta(self):
        cid = self.contenido()
        self.correr()
        self.redes["Instagram"].fallo = ResultadoIncierto("sin respuesta")
        self.reloj.t = T0
        self.correr()
        fila = self.filas()["Instagram"]["id"]
        self.assertEqual(self.estado(fila), "Requiere revisión")
        self.assertEqual(N.leer(self.n.paginas[fila], P.ID_SUBIDA), "subida-1")
        self.n.actualizar(cid, {C.ESTADO: N.opcion("Listo para publicar")})
        self.correr()
        self.assertEqual(len(self.redes["Instagram"].llamadas), 1)
        self.assertEqual(self.estado(fila), "Requiere revisión")

    def test_fecha_muy_pasada(self):
        self.contenido()
        self.reloj.t = T0 + timedelta(hours=5)
        self.correr()
        fila = self.filas()["Instagram"]["id"]
        self.assertEqual(self.estado(fila), "Error")
        self.assertEqual(self.redes["Instagram"].llamadas, [])


class Cambios(Base):
    def test_cancelar(self):
        cid = self.contenido()
        self.correr()
        self.n.actualizar(cid, {C.ESTADO: N.opcion("Cancelado")})
        self.reloj.t = T0
        self.correr()
        self.assertEqual(self.estado(self.filas()["Instagram"]["id"]), "Cancelado")
        self.assertEqual(self.redes["Instagram"].llamadas, [])

    def test_pausar_con_borrador(self):
        cid = self.contenido()
        self.correr()
        self.n.actualizar(cid, {C.ESTADO: N.opcion("Borrador")})
        self.reloj.t = T0
        self.correr()
        self.assertEqual(self.redes["Instagram"].llamadas, [])

    def test_cambio_de_fecha_en_cola(self):
        cid = self.contenido()
        self.correr()
        self.n.actualizar(cid, {C.FECHA: {"date": {"start": "2026-10-07T18:00:00.000-05:00"}}})
        self.reloj.t = T0
        self.correr()
        self.assertEqual(self.redes["Instagram"].llamadas, [])
        self.reloj.t = T0 + timedelta(days=1)
        self.correr()
        self.assertEqual(len(self.redes["Instagram"].llamadas), 1)

    def test_cambio_de_fecha_ya_programado_se_reprograma(self):
        cid = self.contenido(**{C.REDES: {"multi_select": [{"name": "Facebook"}]}})
        self.correr()
        self.n.actualizar(cid, {C.FECHA: {"date": {"start": "2026-10-07T18:00:00.000-05:00"}}})
        self.correr()
        self.assertEqual(self.redes["Facebook"].llamadas[-1], ("reprogramar", T0 + timedelta(days=1)))

    def test_recuperacion_tras_corte(self):
        self.contenido()
        self.correr()
        fila = self.filas()["Instagram"]
        self.n.actualizar(fila["id"], {P.ESTADO: N.opcion("Subiendo"), P.ID_SUBIDA: N.texto("cont-9"),
                                       P.BLOQUEO: N.texto(f"run=viejo|etapa=subida|desde={(T0 - timedelta(hours=1)).isoformat()}")})
        self.reloj.t = T0 - timedelta(minutes=30)
        self.correr()
        self.assertEqual(self.redes["Instagram"].llamadas, [("recuperar", "cont-9")])
        self.assertEqual(self.estado(fila["id"]), "En cola")   # la red confirmó que no se publicó


if __name__ == "__main__":
    unittest.main()
