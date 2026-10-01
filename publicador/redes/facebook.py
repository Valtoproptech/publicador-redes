"""Facebook Page: Reels (3–90 s) por la Reels API; videos más largos por el endpoint de videos.

Ambos admiten programación nativa: el video queda subido y Facebook lo publica a la hora.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import requests

from ..errores import ErrorPermanente
from ..http import llamar
from ..reglas import P_PROGRAMADO, P_PUBLICADO
from ..secretos import enmascarar
from .base import REINTENTAR, Adaptador, Resultado, Trabajo

REEL_MAX = 90


class Facebook(Adaptador):
    programa_nativo = True

    def __init__(self, token_sistema: str, page_id: str, version: str, sesion=None):
        self.token_sistema, self.page = token_sistema, page_id
        self.g = f"https://graph.facebook.com/{version}"
        self.gv = f"https://graph-video.facebook.com/{version}"
        self.s = sesion or requests.Session()
        self._token_pagina = None

    @property
    def token(self) -> str:
        if not self._token_pagina:
            r = llamar(self.s, "GET", f"{self.g}/{self.page}", contexto="Facebook: token de página",
                       params={"fields": "access_token"},
                       headers={"Authorization": f"OAuth {self.token_sistema}"}).json()
            if "access_token" not in r:
                raise ErrorPermanente("El usuario del sistema de Meta no tiene acceso a esta página de Facebook.")
            enmascarar(r["access_token"])
            self._token_pagina = r["access_token"]
        return self._token_pagina

    @property
    def h(self) -> dict:
        """Cabecera de autorización con el token de la página (nunca va en la URL)."""
        return {"Authorization": f"OAuth {self.token}"}

    def comprobar(self) -> str:
        return llamar(self.s, "GET", f"{self.g}/{self.page}", contexto="Facebook",
                      params={"fields": "name"}, headers=self.h).json()["name"]

    # ------------------------------------------------------------ publicar / programar

    def programar(self, t, video, portada, guardar_id) -> Resultado:
        return self._subir(t, video, portada, guardar_id, esperar=None)

    def publicar(self, t, video, portada, esperar, guardar_id) -> Resultado:
        return self._subir(t, video, portada, guardar_id, esperar=esperar)

    def _subir(self, t: Trabajo, video, portada: Path | None, guardar_id, esperar) -> Resultado:
        programado = esperar is None
        if video.info.duracion <= REEL_MAX:
            video_id = self._reel(t, video, guardar_id, esperar)
            url = f"https://www.facebook.com/reel/{video_id}"
        else:
            video_id = self._video_largo(t, video, esperar)
            url = f"https://www.facebook.com/{self.page}/videos/{video_id}"
        nota = self._miniatura(video_id, portada) if portada else ""
        if programado:
            return Resultado(P_PROGRAMADO, video_id, url, nota=nota)
        return Resultado(P_PUBLICADO, video_id, url, datetime.now(timezone.utc), nota)

    def _reel(self, t: Trabajo, video, guardar_id, esperar) -> str:
        r = llamar(self.s, "POST", f"{self.g}/{self.page}/video_reels", contexto="Facebook: iniciar Reel",
                   data={"upload_phase": "start"}, headers=self.h).json()
        video_id = r["video_id"]
        guardar_id(video_id)
        with open(video.ruta, "rb") as f:
            llamar(self.s, "POST", r["upload_url"], data=f, contexto="Facebook: subir Reel",
                   headers=self.h | {"offset": "0", "file_size": str(video.ruta.stat().st_size)})
        fin = {"upload_phase": "finish", "video_id": video_id, "description": t.texto}
        if esperar is None:
            fin |= {"video_state": "SCHEDULED", "scheduled_publish_time": int(t.cuando.timestamp())}
        else:
            esperar()
            fin["video_state"] = "PUBLISHED"
        llamar(self.s, "POST", f"{self.g}/{self.page}/video_reels", data=fin, final=True, headers=self.h,
               contexto="Facebook: " + ("programar Reel" if esperar is None else "publicar Reel"))
        return video_id

    def _video_largo(self, t: Trabajo, video, esperar) -> str:
        if video.ruta.stat().st_size > 1_000_000_000:
            raise ErrorPermanente("Facebook: videos de más de 1 GB no están soportados por este sistema.")
        datos = {"description": t.texto}
        if esperar is None:
            datos |= {"published": "false", "scheduled_publish_time": int(t.cuando.timestamp())}
        else:
            esperar()
        with open(video.ruta, "rb") as f:
            r = llamar(self.s, "POST", f"{self.gv}/{self.page}/videos", data=datos, files={"source": f}, headers=self.h,
                       final=True, contexto="Facebook: subir video", timeout=(20, 1800)).json()
        return r["id"]

    def _miniatura(self, video_id: str, portada: Path) -> str:
        try:
            with open(portada, "rb") as f:
                llamar(self.s, "POST", f"{self.g}/{video_id}/thumbnails", files={"source": f},
                       data={"is_preferred": "true"}, headers=self.h, contexto="Facebook: portada")
            return ""
        except Exception as e:
            return f"Portada no aplicada en Facebook ({e})."

    # ------------------------------------------------------------ seguimiento

    def _estado(self, video_id: str) -> dict:
        return llamar(self.s, "GET", f"{self.g}/{video_id}", contexto="Facebook: estado",
                      params={"fields": "published,status,permalink_url,scheduled_publish_time"}, headers=self.h).json()

    def _url(self, e: dict, video_id: str) -> str:
        enlace = e.get("permalink_url") or f"/reel/{video_id}"
        return enlace if enlace.startswith("http") else "https://www.facebook.com" + enlace

    def verificar(self, t: Trabajo) -> Resultado | None:
        e = self._estado(t.id_publicacion)
        if (e.get("status") or {}).get("video_status") == "error":
            raise ErrorPermanente("Facebook marcó el video con error al procesarlo.")
        if e.get("published"):
            return Resultado(P_PUBLICADO, t.id_publicacion, self._url(e, t.id_publicacion), datetime.now(timezone.utc))
        return None

    def recuperar(self, t: Trabajo) -> Resultado | str:
        if not t.id_subida:
            return REINTENTAR
        e = self._estado(t.id_subida)
        if e.get("published"):
            return Resultado(P_PUBLICADO, t.id_subida, self._url(e, t.id_subida), datetime.now(timezone.utc),
                             "Recuperado tras una interrupción.")
        if e.get("scheduled_publish_time"):
            return Resultado(P_PROGRAMADO, t.id_subida, self._url(e, t.id_subida), nota="Recuperado tras una interrupción.")
        return REINTENTAR  # subido pero nunca finalizado: queda como borrador invisible, no se publica

    def reprogramar(self, t: Trabajo, cuando: datetime) -> None:
        llamar(self.s, "POST", f"{self.g}/{t.id_publicacion}", contexto="Facebook: reprogramar",
               data={"scheduled_publish_time": int(cuando.timestamp())}, headers=self.h)
