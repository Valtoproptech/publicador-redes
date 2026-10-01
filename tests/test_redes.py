"""Adaptadores contra respuestas HTTP simuladas (sin cuentas reales)."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import requests

from publicador.config import Destino
from publicador.errores import ErrorPermanente, ResultadoIncierto
from publicador.media import ArchivoPreparado
from publicador.redes.base import REINTENTAR, Trabajo
from publicador.redes.facebook import Facebook
from publicador.redes.instagram import Instagram
from publicador.redes.tiktok import TikTok, plan_trozos
from publicador.redes.uploadpost import UploadPost
from publicador.reglas import InfoVideo


class Resp:
    def __init__(self, codigo=200, datos=None):
        self.status_code, self._datos, self.text = codigo, datos or {}, str(datos)

    def json(self):
        return self._datos


class SesionFalsa:
    """Responde según la primera regla (método, fragmento de URL) que coincida; registra cada llamada."""

    def __init__(self, reglas):
        self.reglas, self.llamadas, self.headers = reglas, [], {}

    def request(self, metodo, url, timeout=None, **kw):
        self.llamadas.append((metodo, url, kw))
        for (m, frag), resp in self.reglas.items():
            if m == metodo and frag in url:
                if isinstance(resp, list):
                    return resp.pop(0) if len(resp) > 1 else resp[0]
                if isinstance(resp, Exception):
                    raise resp
                return resp
        raise AssertionError(f"llamada inesperada {metodo} {url}")


def archivo(duracion=45):
    ruta = Path(tempfile.mkdtemp()) / "v.mp4"
    ruta.write_bytes(b"x" * 1000)
    info = InfoVideo("mov,mp4", "h264", 1080, 1920, 30, duracion, 16_000_000, 1000, "aac", 48000)
    return ArchivoPreparado(ruta, info, "")


T = Trabajo(red="Instagram", cuenta="Bambú", cuando=datetime(2026, 10, 6, 23, tzinfo=timezone.utc),
            titulo="Depa demo", texto="Copy\n\n#bambu", colaboradores=["valto"], portada_url="https://notion/portada.jpg")


class InstagramTest(unittest.TestCase):
    def ig(self, reglas):
        self.s = SesionFalsa(reglas)
        return Instagram("TOK", "IG1", "v24.0", sesion=self.s, dormir=lambda s: None)

    def test_flujo(self):
        ig = self.ig({
            ("POST", "/IG1/media_publish"): Resp(200, {"id": "M1"}),
            ("POST", "/IG1/media"): Resp(200, {"id": "C1", "uri": "https://rupload.facebook.com/ig-api-upload/v24.0/C1"}),
            ("POST", "rupload"): Resp(200, {"success": True}),
            ("GET", "/C1"): [Resp(200, {"status_code": "IN_PROGRESS"}), Resp(200, {"status_code": "FINISHED"})],
            ("GET", "/M1"): Resp(200, {"permalink": "https://instagram.com/reel/abc"}),
        })
        ids, orden = [], []
        r = ig.publicar(T, archivo(), Path("/tmp/p.jpg"), esperar=lambda: orden.append("esperar"), guardar_id=ids.append)
        self.assertEqual((r.estado, r.id_publicacion, r.url), ("Publicado", "M1", "https://instagram.com/reel/abc"))
        self.assertEqual(ids, ["C1"])
        crear = self.s.llamadas[0][2]["data"]
        self.assertEqual(crear["media_type"], "REELS")
        self.assertEqual(crear["upload_type"], "resumable")
        self.assertEqual(crear["collaborators"], '["valto"]')
        self.assertEqual(crear["cover_url"], "https://notion/portada.jpg")
        self.assertNotIn("access_token", crear)                       # el token va en cabecera, no en el cuerpo
        self.assertEqual(self.s.headers["Authorization"], "OAuth TOK")
        subida = self.s.llamadas[1][2]["headers"]
        self.assertEqual((subida["offset"], subida["file_size"]), ("0", "1000"))
        # esperar() se llamó después de que el video quedó procesado y antes de publicar
        self.assertEqual([u for _, u, _ in self.s.llamadas][-2].endswith("media_publish"), True)

    def test_publicacion_sin_respuesta_es_incierta(self):
        ig = self.ig({
            ("POST", "/IG1/media_publish"): requests.Timeout(),
            ("POST", "/IG1/media"): Resp(200, {"id": "C1"}),
            ("POST", "rupload"): Resp(200, {}),
            ("GET", "/C1"): Resp(200, {"status_code": "FINISHED"}),
        })
        with self.assertRaises(ResultadoIncierto):
            ig.publicar(T, archivo(), None, lambda: None, lambda x: None)

    def test_video_rechazado(self):
        ig = self.ig({
            ("POST", "/IG1/media"): Resp(200, {"id": "C1"}),
            ("POST", "rupload"): Resp(200, {}),
            ("GET", "/C1"): Resp(200, {"status_code": "ERROR", "status": "Error: formato"}),
        })
        with self.assertRaisesRegex(ErrorPermanente, "formato"):
            ig.publicar(T, archivo(), None, lambda: None, lambda x: None)

    def test_recuperar(self):
        ig = self.ig({("GET", "/C9"): Resp(200, {"status_code": "FINISHED"})})
        t = Trabajo(**{**T.__dict__, "id_subida": "C9"})
        self.assertEqual(ig.recuperar(t), REINTENTAR)
        ig = self.ig({("GET", "/C9"): Resp(200, {"status_code": "PUBLISHED"}),
                      ("GET", "/IG1/media"): Resp(200, {"data": [{"id": "M9", "caption": "Copy\n\n#bambu"}]}),
                      ("GET", "/M9"): Resp(200, {"permalink": "https://ig/p"})})
        r = ig.recuperar(t)
        self.assertEqual((r.estado, r.id_publicacion), ("Publicado", "M9"))


class FacebookTest(unittest.TestCase):
    def test_programa_reel(self):
        s = SesionFalsa({
            ("GET", "/PAGE"): Resp(200, {"access_token": "PT"}),
            ("POST", "/PAGE/video_reels"): [Resp(200, {"video_id": "V1", "upload_url": "https://rupload.facebook.com/video-upload/V1"}),
                                            Resp(200, {"success": True})],
            ("POST", "rupload"): Resp(200, {"success": True}),
        })
        fb = Facebook("SYS", "PAGE", "v24.0", sesion=s)
        t = Trabajo(**{**T.__dict__, "red": "Facebook"})
        r = fb.programar(t, archivo(), None, lambda x: None)
        self.assertEqual((r.estado, r.id_publicacion), ("Programado", "V1"))
        fin = s.llamadas[-1][2]["data"]
        self.assertEqual(fin["video_state"], "SCHEDULED")
        self.assertEqual(fin["scheduled_publish_time"], int(t.cuando.timestamp()))
        self.assertEqual(s.llamadas[-1][2]["headers"]["Authorization"], "OAuth PT")
        self.assertNotIn("access_token", fin)

    def test_video_largo_usa_endpoint_de_videos(self):
        s = SesionFalsa({("GET", "/PAGE"): Resp(200, {"access_token": "PT"}),
                         ("POST", "graph-video.facebook.com/v24.0/PAGE/videos"): Resp(200, {"id": "V2"})})
        fb = Facebook("SYS", "PAGE", "v24.0", sesion=s)
        r = fb.programar(Trabajo(**{**T.__dict__, "red": "Facebook"}), archivo(duracion=300), None, lambda x: None)
        self.assertEqual(r.id_publicacion, "V2")
        self.assertEqual(s.llamadas[-1][2]["data"]["published"], "false")


class UploadPostTest(unittest.TestCase):
    def up(self, reglas, **destino):
        self.s = SesionFalsa(reglas)
        d = Destino("TikTok", "uploadpost", {"perfil": "bambu", **destino})
        return UploadPost("KEY", "TikTok", d, sesion=self.s, dormir=lambda s: None)

    def test_tiktok_publico_con_seguimiento(self):
        up = self.up({("POST", "/upload"): Resp(200, {"success": True, "request_id": "R1"}),
                      ("GET", "/uploadposts/status"): [Resp(200, {"status": "processing"}),
                                                        Resp(200, {"results": {"tiktok": {"success": True, "url": "https://tiktok.com/@b/video/1", "post_id": "1"}}})]})
        ids = []
        r = up.publicar(Trabajo(**{**T.__dict__, "red": "TikTok"}), archivo(), None, lambda: None, ids.append)
        self.assertEqual((r.estado, r.id_publicacion), ("Publicado", "1"))
        self.assertEqual(ids, ["req:R1"])
        campos = dict(self.s.llamadas[0][2]["data"])
        self.assertEqual(campos["post_mode"], "DIRECT_POST")
        self.assertEqual(campos["privacy_level"], "PUBLIC_TO_EVERYONE")
        self.assertEqual(campos["title"], "Copy\n\n#bambu")
        self.assertEqual(self.s.headers["Authorization"], "Apikey KEY")

    def test_tiktok_borrador(self):
        up = self.up({("POST", "/upload"): Resp(200, {"results": {"tiktok": {"success": True}}})}, modo="borrador")
        r = up.publicar(Trabajo(**{**T.__dict__, "red": "TikTok"}), archivo(), None, lambda: None, lambda x: None)
        self.assertEqual(r.estado, "En borradores")
        self.assertEqual(dict(self.s.llamadas[0][2]["data"])["post_mode"], "MEDIA_UPLOAD")

    def test_rechazo(self):
        up = self.up({("POST", "/upload"): Resp(200, {"results": {"tiktok": {"success": False, "error": "spam"}}})})
        with self.assertRaisesRegex(ErrorPermanente, "spam"):
            up.publicar(Trabajo(**{**T.__dict__, "red": "TikTok"}), archivo(), None, lambda: None, lambda x: None)


class SecretosMemoria:
    def __init__(self, **d):
        self.d = d

    def leer(self, n):
        return self.d[n]

    def guardar(self, n, v):
        self.d[n] = v


class TikTokTest(unittest.TestCase):
    CRED = '{"client_key": "K", "client_secret": "S", "access_token": "VIEJO", "refresh_token": "R1", "expira": 0}'

    def test_trozos_segun_reglas_de_tiktok(self):
        MB = 1024 * 1024
        self.assertEqual(plan_trozos(3 * MB), (3 * MB, 1))
        self.assertEqual(plan_trozos(40 * MB), (32 * MB, 1))       # el último trozo absorbe el resto
        self.assertEqual(plan_trozos(200 * MB), (32 * MB, 6))

    def test_borrador_renovando_token(self):
        s = SesionFalsa({
            ("POST", "/oauth/token/"): Resp(200, {"access_token": "NUEVO", "refresh_token": "R2", "expires_in": 86400}),
            ("POST", "/inbox/video/init/"): Resp(200, {"data": {"publish_id": "P1", "upload_url": "https://up/1"}, "error": {"code": "ok"}}),
            ("PUT", "https://up/1"): Resp(201, {}),
            ("POST", "/status/fetch/"): [Resp(200, {"data": {"status": "PROCESSING_UPLOAD"}, "error": {"code": "ok"}}),
                                         Resp(200, {"data": {"status": "SEND_TO_USER_INBOX"}, "error": {"code": "ok"}})],
        })
        sec = SecretosMemoria(**{"tiktok-bambu": self.CRED})
        tt = TikTok("tiktok-bambu", sec, sesion=s, dormir=lambda x: None)
        ids = []
        r = tt.publicar(Trabajo(**{**T.__dict__, "red": "TikTok"}), archivo(), None, lambda: None, ids.append)
        self.assertEqual((r.estado, r.id_publicacion), ("En borradores", "P1"))
        self.assertEqual(ids, ["P1"])
        self.assertIn('"refresh_token": "R2"', sec.d["tiktok-bambu"])      # token renovado y guardado
        put = [c for c in s.llamadas if c[0] == "PUT"][0][2]["headers"]
        self.assertEqual(put["Content-Range"], "bytes 0-999/1000")
        self.assertEqual(s.llamadas[1][2]["headers"]["Authorization"], "Bearer NUEVO")

    def test_limite_de_borradores(self):
        s = SesionFalsa({("POST", "/inbox/video/init/"): Resp(200, {"error": {"code": "spam_risk_too_many_pending_share", "message": "x"}})})
        cred = self.CRED.replace('"expira": 0', '"expira": 9999999999')
        tt = TikTok("tiktok-bambu", SecretosMemoria(**{"tiktok-bambu": cred}), sesion=s)
        with self.assertRaisesRegex(ErrorPermanente, "5 borradores"):
            tt.publicar(Trabajo(**{**T.__dict__, "red": "TikTok"}), archivo(), None, lambda: None, lambda x: None)


class SecretosTest(unittest.TestCase):
    def test_cifrado_ida_y_vuelta(self):
        from publicador.secretos import SecretosCifrados, nueva_clave
        ruta = Path(tempfile.mkdtemp()) / "s.cifrado"
        clave = nueva_clave()
        SecretosCifrados(clave, ruta).guardar("meta", "TOKEN-SECRETO")
        self.assertNotIn(b"TOKEN-SECRETO", ruta.read_bytes())
        self.assertEqual(SecretosCifrados(clave, ruta).leer("meta"), "TOKEN-SECRETO")
        with self.assertRaises(Exception):
            SecretosCifrados(nueva_clave(), ruta)                    # otra clave no puede leerlo


if __name__ == "__main__":
    unittest.main()
