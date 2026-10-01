"""Interfaz común de las redes. Cada adaptador traduce 'publica esto' a la API de su plataforma."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from ..errores import ErrorPermanente, ResultadoIncierto
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
