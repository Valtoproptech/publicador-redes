"""Pruebas con videos reales generados por ffmpeg (requiere ffmpeg instalado)."""
import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path

from publicador.media import analizar, preparar


def generar(ruta: Path, ancho: int, alto: int, extra=()):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=s={ancho}x{alto}:r=30:d=4",
                    "-f", "lavfi", "-i", "sine=f=440:d=4:sample_rate=48000", "-shortest",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", *extra, str(ruta)],
                   check=True)


def md5(ruta: Path) -> str:
    return hashlib.md5(ruta.read_bytes()).hexdigest()


class Media(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_original_compatible_no_se_toca(self):
        v = self.tmp / "ok.mp4"
        generar(v, 1080, 1920, ["-movflags", "+faststart"])
        antes = md5(v)
        for red in ("Instagram", "Facebook", "TikTok", "YouTube"):
            p = preparar(v, red, self.tmp)
            self.assertEqual(p.ruta, v, red)
            self.assertTrue(p.detalle.startswith("Original intacto"))
        self.assertEqual(md5(v), antes)

    def test_indice_al_final_se_reordena_sin_perder_calidad(self):
        v = self.tmp / "sin-faststart.mp4"
        generar(v, 1080, 1920)
        self.assertFalse(analizar(v).moov_al_inicio)
        p = preparar(v, "Instagram", self.tmp)
        self.assertIn("Reordenado sin recomprimir", p.detalle)
        self.assertTrue(p.info.moov_al_inicio)
        mismo = lambda r: subprocess.run(["ffmpeg", "-v", "error", "-i", str(r), "-map", "0:v", "-f", "md5", "-"],
                                         capture_output=True, text=True).stdout
        self.assertEqual(mismo(v), mismo(p.ruta))   # mismos fotogramas decodificados, bit a bit

    def test_4k_se_recodifica_solo_para_instagram(self):
        v = self.tmp / "4k.mp4"
        generar(v, 2160, 3840, ["-movflags", "+faststart"])
        ig = preparar(v, "Instagram", self.tmp)
        self.assertIn("RECODIFICADO para Instagram", ig.detalle)
        self.assertEqual((ig.info.ancho, ig.info.alto), (1080, 1920))
        self.assertEqual(preparar(v, "TikTok", self.tmp).ruta, v)


if __name__ == "__main__":
    unittest.main()
