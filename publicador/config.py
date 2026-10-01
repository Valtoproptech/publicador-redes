"""Carga config.toml (IDs y opciones; nunca tokens: esos van en secretos).

En GitHub Actions el contenido llega por la variable PUBLICADOR_CONFIG_TOML (un secret de GitHub),
así los IDs de tus cuentas no quedan en el repositorio público.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .errores import ErrorValidacion
from .reglas import REDES

VIAS = ("nativo", "uploadpost")
REQUERIDOS = {
    ("Instagram", "nativo"): ("ig_user_id",),
    ("Facebook", "nativo"): ("page_id",),
    ("YouTube", "nativo"): ("secreto",),
    ("TikTok", "nativo"): ("secreto",),
    ("Instagram", "uploadpost"): ("perfil",),
    ("Facebook", "uploadpost"): ("perfil", "page_id"),
    ("YouTube", "uploadpost"): ("perfil",),
}


@dataclass
class Destino:
    red: str
    via: str
    datos: dict

    def get(self, clave: str, defecto=None):
        return self.datos.get(clave, defecto)


@dataclass
class Config:
    notion_contenidos: str
    notion_publicaciones: str
    zona_horaria: str = "America/Lima"
    # Con cuánta antelación una ejecución toma una publicación "a la hora" y espera el minuto exacto.
    # En GitHub Actions el cron puede atrasarse, por eso el valor por defecto es amplio.
    anticipacion_minutos: int = 45
    graph_version: str = "v25.0"
    cuentas: dict[str, dict[str, Destino]] = field(default_factory=dict)

    def destino(self, cuenta: str, red: str) -> Destino:
        try:
            return self.cuentas[cuenta][red]
        except KeyError:
            raise ErrorValidacion(f"La cuenta '{cuenta}' no tiene {red} configurado.") from None


def cargar(ruta: str | Path | None = None) -> Config:
    texto = os.environ.get("PUBLICADOR_CONFIG_TOML") if ruta is None else None
    if not texto:
        texto = Path(ruta or os.environ.get("PUBLICADOR_CONFIG", "config.toml")).read_text(encoding="utf-8")
    datos = tomllib.loads(texto)
    cuentas: dict[str, dict[str, Destino]] = {}
    for cuenta, redes in datos.get("cuentas", {}).items():
        cuentas[cuenta] = {}
        for red, d in redes.items():
            red = _red_canonica(red)
            via = d.get("via", "nativo")
            if via not in VIAS:
                raise ValueError(f"[cuentas.'{cuenta}'.{red}] via debe ser uno de {VIAS}")
            if (red, via) not in REQUERIDOS:
                raise ValueError(f"[cuentas.'{cuenta}'.{red}] {red} no se puede publicar con via='{via}'.")
            faltan = [k for k in REQUERIDOS[(red, via)] if not d.get(k)]
            if faltan:
                raise ValueError(f"[cuentas.'{cuenta}'.{red}] falta: {', '.join(faltan)}")
            cuentas[cuenta][red] = Destino(red, via, d)
    n = datos.get("notion", {})
    return Config(
        notion_contenidos=n.get("contenidos", ""),
        notion_publicaciones=n.get("publicaciones", ""),
        zona_horaria=datos.get("zona_horaria", "America/Lima"),
        anticipacion_minutos=int(datos.get("anticipacion_minutos", 45)),
        graph_version=datos.get("graph_version", "v25.0"),
        cuentas=cuentas,
    )


def _red_canonica(nombre: str) -> str:
    for red in REDES:
        if red.lower() == nombre.lower():
            return red
    raise ValueError(f"Red desconocida en config: {nombre}. Usa una de {REDES}")
