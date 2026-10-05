"""Instagram Reels vía Graph API (Facebook Login + token de usuario del sistema de Meta).

Flujo: contenedor REELS resumable → subir bytes originales a rupload → esperar FINISHED
→ (esperar la hora) → media_publish.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
import requests

from ..errores import ErrorPermanente, ErrorTransitorio
from ..http import llamar
from ..reglas import P_PUBLICADO
from .base import ESPERAR, REINTENTAR, Adaptador, Resultado, Trabajo


class Instagram(Adaptador):
    def __init__(self, token: str, ig_user_id: str, version: str, sesion=None, dormir=time.sleep):
        self.ig = ig_user_id
        self.g = f"https://graph.facebook.com/{version}"
        self.version = version
        self.s = sesion or requests.Session()
        self.s.headers["Authorization"] = f"OAuth {token}"   # en cabecera: nunca aparece en URLs ni registros
        self.dormir = dormir

    def _get(self, ruta: str, **params) -> dict:
        return llamar(self.s, "GET", f"{self.g}/{ruta}", params=params, contexto="Instagram").json()

    def comprobar(self) -> str:
        return "@" + self._get(self.ig, fields="username")["username"]

    def publicar(self, t: Trabajo, video, portada, esperar, guardar_id) -> Resultado:
        datos = {"media_type": "REELS", "upload_type": "resumable", "caption": t.texto, "share_to_feed": "true"}
        if t.colaboradores:
            datos["collaborators"] = json.dumps(t.colaboradores)
        if t.portada_url:
            datos["cover_url"] = t.portada_url
        nota = ""
        r = llamar(self.s, "POST", f"{self.g}/{self.ig}/media", data=datos, contexto="Instagram: crear contenedor").json()
        contenedor = r["id"]
        guardar_id(contenedor)
        uri = r.get("uri") or f"https://rupload.facebook.com/ig-api-upload/{self.version}/{contenedor}"
        with open(video.ruta, "rb") as f:
            llamar(self.s, "POST", uri, data=f, contexto="Instagram: subir video",
                   headers={"offset": "0", "file_size": str(video.ruta.stat().st_size)})
        self._esperar_procesado(contenedor)
        esperar()
        r = llamar(self.s, "POST", f"{self.g}/{self.ig}/media_publish", final=True, contexto="Instagram: publicar",
                   data={"creation_id": contenedor}).json()
        return self._resultado(r["id"], nota)

    # ------------------------------------------------------------ fotos y carruseles

    fotos_por_url = True   # Instagram solo acepta fotos por URL pública: el motor las aloja en Notion

    def publicar_fotos(self, t: Trabajo, album, esperar, guardar_id) -> Resultado:
        """1 foto → post de imagen; 2 a 10 → carrusel. Mismo cierre que los Reels: esperar → media_publish."""
        crear = lambda datos, contexto: llamar(self.s, "POST", f"{self.g}/{self.ig}/media", data=datos,
                                               contexto=contexto).json()["id"]
        final = {"caption": t.texto}
        if t.colaboradores:
            final["collaborators"] = json.dumps(t.colaboradores)
        if len(album.fotos) == 1:
            contenedor = crear(final | {"image_url": album.fotos[0].url}, "Instagram: crear contenedor de foto")
        else:
            hijos = []
            for i, foto in enumerate(album.fotos, 1):
                hijo = crear({"image_url": foto.url, "is_carousel_item": "true"}, f"Instagram: foto {i}")
                self._esperar_procesado(hijo)
                hijos.append(hijo)
            contenedor = crear(final | {"media_type": "CAROUSEL", "children": ",".join(hijos)},
                               "Instagram: crear carrusel")
        guardar_id(contenedor)
        self._esperar_procesado(contenedor)
        esperar()
        r = llamar(self.s, "POST", f"{self.g}/{self.ig}/media_publish", final=True, contexto="Instagram: publicar",
                   data={"creation_id": contenedor}).json()
        nota = ""
        proporciones = {round(f.info.ancho / f.info.alto, 2) for f in album.fotos}
        if len(proporciones) > 1:
            nota = "Las fotos tienen proporciones distintas: Instagram las recorta todas al formato de la primera."
        return self._resultado(r["id"], nota)

    def _resultado(self, media_id: str | None, nota: str = "") -> Resultado:
        enlace = None
        if media_id:
            try:
                enlace = self._get(media_id, fields="permalink").get("permalink")
            except Exception:
                nota = (nota + " No pude leer el enlace del post.").strip()
        return Resultado(P_PUBLICADO, media_id, enlace, datetime.now(timezone.utc), nota)

    def _esperar_procesado(self, contenedor: str, limite: int = 900) -> None:
        inicio = time.monotonic()
        while time.monotonic() - inicio < limite:
            estado = self._get(contenedor, fields="status_code,status")
            codigo = estado.get("status_code")
            if codigo == "FINISHED":
                return
            if codigo in ("ERROR", "EXPIRED"):
                raise ErrorPermanente(f"Instagram rechazó el archivo al procesarlo: {estado.get('status')}")
            self.dormir(10)
        raise ErrorTransitorio("Instagram tardó más de 15 min en procesar el archivo.")

    def recuperar(self, t: Trabajo) -> Resultado | str:
        if not t.id_subida:
            return REINTENTAR  # el corte fue antes de crear el contenedor: no hay nada en Instagram
        codigo = self._get(t.id_subida, fields="status_code").get("status_code")
        if codigo == "PUBLISHED":
            return self._resultado(self._buscar_media(t), "Recuperado tras una interrupción.")
        if codigo == "IN_PROGRESS":
            return ESPERAR
        return REINTENTAR  # FINISHED sin publicar, ERROR o EXPIRED: no está publicado

    def _buscar_media(self, t: Trabajo) -> str | None:
        recientes = self._get(f"{self.ig}/media", fields="id,caption,timestamp", limit=15).get("data", [])
        for m in recientes:
            if (m.get("caption") or "").strip() == t.texto.strip():
                return m["id"]
        return None
