import unittest
from datetime import datetime, timedelta, timezone

from publicador.errores import ErrorValidacion
from publicador.reglas import (InfoVideo, componer_texto, diagnosticar, estado_contenido, id_de_drive,
                               normalizar_hashtags, parsear_fecha, planificar, validar_texto)

T0 = datetime(2026, 10, 6, 23, 0, tzinfo=timezone.utc)
ANT = timedelta(minutes=6)


def plan(**kw):
    base = dict(red="Instagram", via="nativo", estado="En cola", cuando=T0, ahora=T0, anticipacion=ANT)
    base.update(kw)
    return planificar(**base).accion


class Drive(unittest.TestCase):
    def test_formatos(self):
        self.assertEqual(id_de_drive("https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/view?usp=drive_link"),
                         "1AbCdEfGhIjKlMnOpQrStUvWxYz012345")
        self.assertEqual(id_de_drive("https://drive.google.com/open?id=1AbCdEfGhIjKlMnOpQr"), "1AbCdEfGhIjKlMnOpQr")

    def test_carpeta_y_vacio(self):
        with self.assertRaisesRegex(ErrorValidacion, "carpeta"):
            id_de_drive("https://drive.google.com/drive/folders/17LD5EJSZEVGUfIRpC5MiOiz9Tojaly_g")
        with self.assertRaises(ErrorValidacion):
            id_de_drive("")
        with self.assertRaises(ErrorValidacion):
            id_de_drive("https://youtube.com/watch?v=x")


class Fechas(unittest.TestCase):
    def test_con_zona(self):
        self.assertEqual(parsear_fecha("2026-10-06T18:00:00.000-05:00", "America/Lima"), T0)

    def test_sin_zona_usa_lima(self):
        self.assertEqual(parsear_fecha("2026-10-06T18:00:00", "America/Lima"), T0)

    def test_sin_hora(self):
        with self.assertRaisesRegex(ErrorValidacion, "hora"):
            parsear_fecha("2026-10-06", "America/Lima")


class Texto(unittest.TestCase):
    def test_hashtags(self):
        self.assertEqual(normalizar_hashtags("inmobiliaria, #Lima  depas #lima"), ["#inmobiliaria", "#Lima", "#depas"])
        self.assertEqual(componer_texto("Hola", "lima"), "Hola\n\n#lima")
        self.assertEqual(componer_texto("", ""), "")

    def test_limites(self):
        with self.assertRaises(ErrorValidacion):
            validar_texto("Instagram", " ".join(f"#t{i}" for i in range(31)))
        validar_texto("TikTok", " ".join(f"#t{i}" for i in range(31)))
        with self.assertRaises(ErrorValidacion):
            validar_texto("TikTok", "x" * 2201)


def video(**kw):
    base = dict(formato="mov,mp4,m4a,3gp,3g2,mj2", codec_video="h264", ancho=1080, alto=1920, fps=30,
                duracion=45, bitrate_video=16_000_000, peso=90_000_000, codec_audio="aac", muestreo_audio=48000)
    base.update(kw)
    return InfoVideo(**base)


class Calidad(unittest.TestCase):
    def test_master_recomendado_pasa_en_todas(self):
        for red in ("Instagram", "Facebook", "TikTok", "YouTube"):
            self.assertTrue(diagnosticar(video(), red).ok, red)

    def test_4k_solo_se_recodifica_para_instagram(self):
        v = video(ancho=2160, alto=3840)
        self.assertTrue(diagnosticar(v, "Instagram").arreglable)
        self.assertTrue(diagnosticar(v, "TikTok").ok)

    def test_indice_al_final_se_reordena_sin_recomprimir(self):
        d = diagnosticar(video(moov_al_inicio=False), "Instagram")
        self.assertFalse(d.arreglable)
        self.assertTrue(d.solo_contenedor)

    def test_duracion_no_se_arregla(self):
        self.assertTrue(diagnosticar(video(duracion=2), "Instagram").fatal)


class Planificacion(unittest.TestCase):
    def test_instagram_espera_y_luego_a_la_hora(self):
        self.assertEqual(plan(ahora=T0 - timedelta(hours=3)), "esperar")
        self.assertEqual(plan(ahora=T0 - timedelta(minutes=5)), "publicar_a_la_hora")

    def test_facebook_y_youtube_se_programan_en_la_plataforma(self):
        self.assertEqual(plan(red="Facebook", ahora=T0 - timedelta(days=3)), "programar_nativo")
        self.assertEqual(plan(red="YouTube", ahora=T0 - timedelta(days=60)), "programar_nativo")
        self.assertEqual(plan(red="Facebook", ahora=T0 - timedelta(days=40)), "esperar")
        self.assertEqual(plan(red="Facebook", ahora=T0 - timedelta(minutes=10)), "esperar")
        self.assertEqual(plan(red="Facebook", ahora=T0 - timedelta(minutes=5)), "publicar_a_la_hora")

    def test_uploadpost_no_usa_programacion_nativa(self):
        self.assertEqual(plan(red="YouTube", via="uploadpost", ahora=T0 - timedelta(days=3)), "esperar")

    def test_fecha_pasada(self):
        self.assertEqual(plan(ahora=T0 + timedelta(minutes=30)), "publicar_a_la_hora")
        self.assertEqual(plan(ahora=T0 + timedelta(hours=3)), "vencido")

    def test_programado_se_verifica_despues(self):
        self.assertEqual(plan(estado="Programado", ahora=T0 + timedelta(minutes=1)), "esperar")
        self.assertEqual(plan(estado="Programado", ahora=T0 + timedelta(minutes=6)), "verificar")

    def test_subiendo(self):
        self.assertEqual(plan(estado="Subiendo", bloqueo_desde=T0 - timedelta(minutes=5)), "esperar")
        self.assertEqual(plan(estado="Subiendo", bloqueo_desde=T0 - timedelta(minutes=20)), "recuperar")
        self.assertEqual(plan(estado="Subiendo", bloqueo_desde=None), "recuperar")


class EstadoContenido(unittest.TestCase):
    def test_resumen(self):
        self.assertEqual(estado_contenido(["Publicado", "Publicado"]), "Publicado")
        self.assertEqual(estado_contenido(["Publicado", "Programado"]), "Programado")
        self.assertEqual(estado_contenido(["Publicado", "Error"]), "Requiere atención")
        self.assertEqual(estado_contenido(["Publicado", "En borradores"]), "Requiere atención")
        self.assertEqual(estado_contenido(["Publicado", "Cancelado"]), "Publicado")
        self.assertEqual(estado_contenido(["Cancelado"]), "Cancelado")


if __name__ == "__main__":
    unittest.main()
