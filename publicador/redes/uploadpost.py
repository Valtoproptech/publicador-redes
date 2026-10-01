"""Upload-Post: intermediario con app de TikTok ya auditada (publica en público y en automático).

Se usa para TikTok (la única forma de publicar ahí sin intervención humana) y como respaldo
configurable para cualquier otra red. Se le envía el ARCHIVO ORIGINAL (no un enlace), y el
sistema sigue siendo quien decide la hora: se llama a la hora exacta.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from ..errores import ErrorPermanente
from ..http import llamar
from ..reglas import P_BORRADORES, P_PUBLICADO, P_SUBIENDO, limpiar_para_youtube
from .base import ESPERAR, Adaptador, Resultado, Trabajo

BASE = "https://api.upload-post.com/api"
PLATAFORMA = {"TikTok": "tiktok", "Instagram": "instagram", "Facebook": "facebook", "YouTube": "youtube"}
PENDIENTE = {"pending", "processing", "in_progress", "queued", "uploading", "scheduled", "started"}
FALLIDO = {"failed", "error", "cancelled", "canceled"}


class UploadPost(Adaptador):
    def __init__(self, api_key: str, red: str, destino, sesion=None, dormir=time.sleep, espera_max: int = 900):
        self.red, self.plat = red, PLATAFORMA[red]
        self.perfil = destino.get("perfil")
        self.modo = destino.get("modo", "publicar")          # TikTok: "publicar" o "borrador"
        self.page_id = destino.get("page_id")
        self.s = sesion or requests.Session()
        self.s.headers["Authorization"] = f"Apikey {api_key}"
        self.dormir, self.espera_max = dormir, espera_max

    def comprobar(self) -> str:
        j = llamar(self.s, "GET", f"{BASE}/uploadposts/users", contexto="Upload-Post").json()
        perfiles = j.get("profiles") or j.get("users") or []
        for p in perfiles:
            if p.get("username") == self.perfil:
                conectadas = p.get("social_accounts") or {}
                if conectadas and not conectadas.get(self.plat):
                    raise ErrorPermanente(f"El perfil '{self.perfil}' no tiene {self.red} conectado en Upload-Post.")
                return f"perfil {self.perfil}"
        raise ErrorPermanente(f"No existe el perfil '{self.perfil}' en Upload-Post.")

    def _campos(self, t: Trabajo, video) -> list[tuple[str, str]]:
        c = [("user", self.perfil), ("platform[]", self.plat), ("async_upload", "true")]
        if self.red == "TikTok":
            c += [("title", t.texto), ("privacy_level", "PUBLIC_TO_EVERYONE"),
                  ("post_mode", "MEDIA_UPLOAD" if self.modo == "borrador" else "DIRECT_POST")]
        elif self.red == "Instagram":
            c += [("title", t.texto), ("media_type", "REELS"), ("share_to_feed", "true")]
            if t.colaboradores:
                c.append(("collaborators", ",".join(t.colaboradores)))
        elif self.red == "Facebook":
            c += [("title", t.titulo), ("facebook_description", t.texto), ("facebook_page_id", self.page_id),
                  ("facebook_media_type", "REELS" if video.info.duracion <= 90 else "VIDEO")]
        elif self.red == "YouTube":
            c += [("title", limpiar_para_youtube(t.titulo_youtube or t.titulo, 100)),
                  ("youtube_description", limpiar_para_youtube(t.texto, 4900)),
                  ("privacyStatus", "public"), ("selfDeclaredMadeForKids", "false")]
        return c

    _CAMPO_PORTADA = {"TikTok": "tiktok_cover_image", "Instagram": "cover_image", "YouTube": "thumbnail"}

    def publicar(self, t: Trabajo, video, portada: Path | None, esperar, guardar_id) -> Resultado:
        esperar()
        archivos = {"video": open(video.ruta, "rb")}
        if portada and self.red in self._CAMPO_PORTADA:
            archivos[self._CAMPO_PORTADA[self.red]] = open(portada, "rb")
        try:
            r = llamar(self.s, "POST", f"{BASE}/upload", data=self._campos(t, video), files=archivos,
                       final=True, contexto=f"Upload-Post ({self.red})", timeout=(20, 1800)).json()
        finally:
            for f in archivos.values():
                f.close()
        if r.get("request_id"):
            guardar_id("req:" + r["request_id"])
            return self._seguir(r["request_id"])
        return self._interpretar(r) or Resultado(P_SUBIENDO, nota=f"Respuesta sin estado: {json.dumps(r)[:300]}")

    def _seguir(self, request_id: str) -> Resultado:
        inicio = time.monotonic()
        while time.monotonic() - inicio < self.espera_max:
            res = self._consultar(request_id)
            if res:
                return res
            self.dormir(15)
        return Resultado(P_SUBIENDO, nota="Upload-Post sigue procesando; se revisará en la próxima ejecución.")

    def _consultar(self, request_id: str) -> Resultado | None:
        r = llamar(self.s, "GET", f"{BASE}/uploadposts/status", params={"request_id": request_id},
                   contexto="Upload-Post: estado").json()
        return self._interpretar(r)

    def _interpretar(self, r: dict) -> Resultado | None:
        """Lee la respuesta de forma defensiva: la documentación no fija el formato exacto."""
        nodo = (r.get("results") or {}).get(self.plat) or r.get(self.plat)
        if isinstance(nodo, list):
            nodo = nodo[0] if nodo else None
        general = str(r.get("status", "")).lower()
        if isinstance(nodo, dict):
            estado = str(nodo.get("status", "")).lower()
            exito = nodo.get("success")
            if exito is True or estado in ("success", "completed", "published", "done"):
                est = P_BORRADORES if self.modo == "borrador" and self.red == "TikTok" else P_PUBLICADO
                return Resultado(est, str(nodo.get("post_id") or nodo.get("publish_id") or nodo.get("video_id") or
                                          nodo.get("id") or "") or None,
                                 nodo.get("url") or nodo.get("post_url"), datetime.now(timezone.utc))
            if exito is False or estado in FALLIDO:
                raise ErrorPermanente(f"{self.red} rechazó la publicación: {nodo.get('error') or nodo.get('message') or nodo}")
            return None
        if general in FALLIDO:
            raise ErrorPermanente(f"Upload-Post: {r.get('error') or r.get('message') or r}")
        if general in PENDIENTE or not general:
            return None
        return None

    def recuperar(self, t: Trabajo) -> Resultado | str:
        if not t.id_subida or not t.id_subida.startswith("req:"):
            return super().recuperar(t)  # sin request_id no hay forma de saber si llegó: revisión humana
        return self._consultar(t.id_subida[4:]) or ESPERAR
