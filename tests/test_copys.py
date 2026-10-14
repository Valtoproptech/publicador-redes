"""Copy automático: GitHub espera el copy que escribe Claude desde la Mac; copys.py lista y guarda."""
import unittest
from datetime import timedelta
from pathlib import Path

from publicador import copys
from publicador import notion as N
from publicador.config import Destino
from publicador.copys import red_del_copy
from publicador.esquema import C
from publicador.motor import Motor
from tests.test_motor import T0, Base, MediosFalsos


class CopyAutomatico(Base):
    def setUp(self):
        super().setUp()
        self.cfg.cuentas["Franco Soler"] = {"Instagram": Destino("Instagram", "nativo", {"ig_user_id": "3"}),
                                            "TikTok": Destino("TikTok", "uploadpost", {"perfil": "franco"})}
        self.cfg.copy_automatico = {"Franco Soler": "copy-redes-franco"}

    def correr(self):
        def dormir(s):
            self.reloj.t += timedelta(seconds=s)
        return Motor(self.cfg, self.n, lambda cuenta, red: self.redes[red], MediosFalsos(), reloj=self.reloj,
                     dormir=dormir).ejecutar()

    def video(self, estado="Borrador", **kw):
        props = {C.CUENTA: N.opcion("Franco Soler"), C.ESTADO: N.opcion(estado), C.COPY: N.texto(""),
                 C.HASHTAGS: N.texto("")}
        props.update(kw)
        return self.contenido(**props)

    def leer(self, cid, prop):
        return N.leer(self.n.paginas[cid], prop)

    def test_modo_por_defecto_en_cuentas_con_estilo(self):
        franco, bambu = self.video(), self.contenido(**{C.ESTADO: N.opcion("Borrador")})
        self.correr()
        self.assertEqual(self.leer(franco, C.MODO_COPY), "Crear copy")
        self.assertIsNone(self.leer(bambu, C.MODO_COPY))

    def test_pendientes_lista_solo_videos_que_esperan_copy(self):
        espera = self.video(**{C.REDES: {"multi_select": [{"name": "TikTok"}]}})
        self.video(**{C.MODO_COPY: N.opcion("Copy propio")})
        self.video(**{C.COPY: N.texto("ya escrito")})
        self.video(**{C.URL: N.enlace("https://drive.google.com/drive/folders/1armAWCQOMisLiS-1RN-rdbSereO7nUAe")})
        self.contenido(**{C.ESTADO: N.opcion("Borrador"), C.COPY: N.texto("")})   # Bambú
        lista = copys.pendientes(self.n, self.cfg)
        self.assertEqual([p["id"] for p in lista], [espera])
        self.assertEqual((lista[0]["red"], lista[0]["skill"]), ("TikTok", "copy-redes-franco"))

    def test_listo_sin_copy_espera_y_con_copy_se_programa(self):
        cid = self.video(estado="Listo para publicar")
        self.correr()
        self.assertEqual(self.filas(cid), {})              # no se programó sin copy
        self.assertEqual(self.leer(cid, C.ESTADO), "Listo para publicar")
        copys.guardar(self.n, cid, "Los leads no leen 😅", "leads, #inmobiliaria")
        self.correr()
        self.assertEqual(self.leer(cid, C.ESTADO), "Programado")
        self.reloj.t = T0 - timedelta(minutes=4)
        self.correr()
        self.assertEqual(self.redes["Instagram"].llamadas[0][1], "Los leads no leen 😅\n\n#leads #inmobiliaria")

    def test_guardar_no_pisa_copy_ni_hashtags(self):
        cid = self.video(**{C.HASHTAGS: N.texto("#mios")})
        copys.guardar(self.n, cid, "Nuevo", "#otros")
        self.assertEqual((self.leer(cid, C.COPY), self.leer(cid, C.HASHTAGS)), ("Nuevo", "#mios"))
        with self.assertRaises(ValueError):
            copys.guardar(self.n, cid, "Otro", "")

    def test_crear_copy_en_cuenta_sin_estilo_avisa(self):
        cid = self.contenido(**{C.COPY: N.texto(""), C.MODO_COPY: N.opcion("Crear copy")})   # Bambú
        self.correr()
        self.assertEqual(self.leer(cid, C.ESTADO), "Error")
        self.assertIn("Copy propio", self.leer(cid, C.ERROR))


class Estilo(unittest.TestCase):
    def test_red_del_copy(self):
        self.assertEqual(red_del_copy(["TikTok"]), "TikTok")
        self.assertEqual(red_del_copy(["TikTok", "Instagram"]), "Instagram")
        self.assertIsNone(red_del_copy(["Facebook"]))

    def test_el_skill_existe(self):
        raiz = Path(__file__).resolve().parent.parent / "skills" / "copy-redes-franco"
        self.assertIn("### TikTok", (raiz / "SKILL.md").read_text())
        self.assertTrue((raiz / "references" / "copys-instagram.md").exists())


if __name__ == "__main__":
    unittest.main()
