"""Crea el adaptador correcto para cada (cuenta, red) según config.toml."""
from __future__ import annotations

import time

from ..config import Config
from ..google_oauth import ALCANCES_YOUTUBE, credenciales
from .base import Adaptador


def fabrica(cfg: Config, secretos, latido=lambda: None):
    cache: dict[tuple[str, str], Adaptador] = {}

    def dormir(segundos: float) -> None:
        latido()
        time.sleep(segundos)

    def crear(cuenta: str, red: str) -> Adaptador:
        clave = (cuenta, red)
        if clave in cache:
            return cache[clave]
        d = cfg.destino(cuenta, red)
        if d.via == "uploadpost":
            from .uploadpost import UploadPost
            a = UploadPost(secretos.leer("uploadpost"), red, d, dormir=dormir)
        elif red == "Instagram":
            from .instagram import Instagram
            a = Instagram(secretos.leer(d.get("secreto", "meta")), d.get("ig_user_id"), cfg.graph_version, dormir=dormir)
        elif red == "Facebook":
            from .facebook import Facebook
            a = Facebook(secretos.leer(d.get("secreto", "meta")), d.get("page_id"), cfg.graph_version)
        elif red == "YouTube":
            from .youtube import YouTube
            a = YouTube(credenciales(secretos.leer(d.get("secreto")), ALCANCES_YOUTUBE), latido)
        elif red == "TikTok":
            from .tiktok import TikTok
            a = TikTok(d.get("secreto"), secretos, dormir=dormir)
        else:
            raise ValueError(f"Sin adaptador para {red}/{d.via}")
        cache[clave] = a
        return a

    return crear
