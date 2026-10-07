"""Interfaz común de las redes. Cada adaptador traduce 'publica esto' a la API de su plataforma."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from ..errores import ErrorPermanente, ErrorPublicador, ResultadoIncierto
from ..media import ArchivoPreparado


@dataclass
class Trabajo:
    red: str
    cuenta: str
    cuando: datetime
    titulo: str
    texto: str                        # copy + hashtags
    titulo_youtube: str = ""
    colaboradores: list[str] = field(default_factory=list)
    portada_url: str | None = None    # URL temporal de la portada subida a Notion (Instagram la exige por URL)
    id_subida: str | None = None      # ID intermedio guardado en Notion (para conciliar tras un corte)
    id_publicacion: str | None = None
    bloqueo_desde: datetime | None = None


@dataclass
class Resultado:
    estado: str                       # reglas.P_PUBLICADO | P_PROGRAMADO | P_BORRADORES | P_SUBIENDO
    id_publicacion: str | None = None
    url: str | None = None
    publicado_el: datetime | None = None
    nota: str = ""


GuardarId = Callable[[str], None]
Esperar = Callable[[], None]

# recuperar() puede devolver un Resultado, o una de estas dos órdenes:
REINTENTAR = "reintentar"   # comprobado que NO se publicó: seguro volver a la cola
ESPERAR = "esperar"         # la plataforma sigue procesando: volver a mirar más tarde

PREFIJO_STORY = "story:"    # en "ID de subida": las piezas subidas de una secuencia de Stories


class Adaptador:
    programa_nativo = False
    pista_sin_confirmar = ""   # texto extra si la plataforma no confirma la publicación a tiempo

    def programar(self, t: Trabajo, video: ArchivoPreparado, portada: Path | None,
                  guardar_id: GuardarId) -> Resultado:
        raise ErrorPermanente(f"{t.red} no admite programación nativa")

    def publicar(self, t: Trabajo, video: ArchivoPreparado, portada: Path | None,
                 esperar: Esperar, guardar_id: GuardarId) -> Resultado:
        """Prepara todo lo posible, llama a esperar() justo antes del paso final y publica."""
        raise NotImplementedError

    # ------------------------------------------------------------ fotos y carruseles (1 a 10 fotos)
    fotos_por_url = False      # True = la red descarga cada foto desde una URL pública (Instagram)

    def programar_fotos(self, t: Trabajo, album, guardar_id: GuardarId) -> Resultado:
        raise ErrorPermanente(f"{t.red} no admite programar fotos desde aquí.")

    def publicar_fotos(self, t: Trabajo, album, esperar: Esperar, guardar_id: GuardarId) -> Resultado:
        raise ErrorPermanente(f"{t.red} no admite fotos ni carruseles en este sistema. Quítala de 'Redes'.")

    # ------------------------------------------------------------ Stories (ninguna red las programa por API)
    def publicar_story(self, t: Trabajo, secuencia, esperar: Esperar, guardar_id: GuardarId) -> Resultado:
        """Sube todas las piezas, llama a esperar() y publica una Story por pieza, en orden."""
        raise ErrorPermanente(f"{t.red} no admite Stories en este sistema. Quítala de 'Redes'.")

    def verificar(self, t: Trabajo) -> Resultado | None:
        """Tras la hora programada: ¿ya está público? None = aún no."""
        return None

    def recuperar(self, t: Trabajo) -> Resultado | str:
        raise ResultadoIncierto("Una ejecución se interrumpió a mitad de la subida y esta red no permite "
                                "comprobar si se publicó. Revisa el perfil.")

    def reprogramar(self, t: Trabajo, cuando: datetime) -> None:
        raise ErrorPermanente(f"{t.red} no permite cambiar la hora de algo ya programado desde aquí.")

    def comprobar(self) -> str:
        """Para `publicador verificar`: devuelve el nombre de la cuenta conectada o lanza error."""
        raise NotImplementedError


def publicar_en_orden(red: str, pasos: list[Callable[[], str]]) -> list[str]:
    """Publica las Stories una tras otra y devuelve sus IDs.

    Si una falla después de haber publicado otras, nunca se reintenta solo (repetiría las ya
    publicadas): pasa a revisión diciendo cuántas salieron."""
    hechas: list[str] = []
    for i, paso in enumerate(pasos, 1):
        try:
            hechas.append(paso())
        except ErrorPublicador as e:
            if not hechas:
                raise
            raise ResultadoIncierto(f"Se publicaron {len(hechas)} de {len(pasos)} Stories en {red} y la n.º {i} "
                                    f"falló ({e}). Sube a mano las que faltan; no se reintenta solo.") from e
    return hechas
