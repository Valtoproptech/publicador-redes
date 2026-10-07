"""Instagram (Reels, fotos, carruseles y Stories) vía Graph API (Facebook Login + token de usuario del sistema de Meta).

Flujo: contenedor REELS resumable → subir bytes originales a rupload → esperar FINISHED
→ (esperar la hora) → media_publish.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
import requests

from ..errores import ErrorPermanente, ErrorTransitorio, ResultadoIncierto
from ..http import llamar
from ..imagenes import Foto
from ..reglas import P_PUBLICADO
from .base import ESPERAR, PREFIJO_STORY, REINTENTAR, Adaptador, Resultado, Trabajo, publicar_en_orden


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
        contenedor = self._subir_video(datos, video, guardar_id)
        self._esperar_procesado(contenedor)
        esperar()
        return self._resultado(self._publicar_contenedor(contenedor))

    def _subir_video(self, datos: dict, video, guardar_id) -> str:
        """Crea el contenedor resumable, guarda su ID al instante y sube los bytes del archivo."""
        r = llamar(self.s, "POST", f"{self.g}/{self.ig}/media", data=datos, contexto="Instagram: crear contenedor").json()
        contenedor = r["id"]
        guardar_id(contenedor)
        uri = r.get("uri") or f"https://rupload.facebook.com/ig-api-upload/{self.version}/{contenedor}"
        with open(video.ruta, "rb") as f:
            llamar(self.s, "POST", uri, data=f, contexto="Instagram: subir video",
                   headers={"offset": "0", "file_size": str(video.ruta.stat().st_size)})
        return contenedor

    def _publicar_contenedor(self, contenedor: str) -> str:
        return llamar(self.s, "POST", f"{self.g}/{self.ig}/media_publish", final=True, contexto="Instagram: publicar",
                      data={"creation_id": contenedor}).json()["id"]

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
        media_id = self._publicar_contenedor(contenedor)
        nota = ""
        proporciones = {round(f.info.ancho / f.info.alto, 2) for f in album.fotos}
        if len(proporciones) > 1:
            nota = "Las fotos tienen proporciones distintas: Instagram las recorta todas al formato de la primera."
        return self._resultado(media_id, nota)

    # ------------------------------------------------------------ Stories

    def publicar_story(self, t: Trabajo, secuencia, esperar, guardar_id) -> Resultado:
        """Un contenedor STORIES por pieza (fotos por URL, videos resumables). Todos quedan listos antes de la
        hora; después se publican en orden. 'story:C1,C2…' en ID de subida permite conciliar tras un corte."""
        contenedores: list[str] = []
        guardar = lambda: guardar_id(PREFIJO_STORY + ",".join(contenedores))
        for i, pieza in enumerate(secuencia.piezas, 1):
            if isinstance(pieza, Foto):
                contenedor = llamar(self.s, "POST", f"{self.g}/{self.ig}/media", contexto=f"Instagram: Story {i}",
                                    data={"media_type": "STORIES", "image_url": pieza.url}).json()["id"]
            else:
                contenedor = self._subir_video({"media_type": "STORIES", "upload_type": "resumable"}, pieza,
                                               lambda c: None)
            contenedores.append(contenedor)
            guardar()
        for contenedor in contenedores:
            self._esperar_procesado(contenedor)
        esperar()
        ids = publicar_en_orden(t.red, [lambda c=c: self._publicar_contenedor(c) for c in contenedores])
        return self._resultado_story(ids)

    def _resultado_story(self, ids: list[str], nota: str = "") -> Resultado:
        r = self._resultado(ids[0], nota)
        r.id_publicacion = ",".join(ids)
        return r

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
        if t.id_subida.startswith(PREFIJO_STORY):
            return self._recuperar_story(t.id_subida.removeprefix(PREFIJO_STORY).split(","))
        codigo = self._get(t.id_subida, fields="status_code").get("status_code")
        if codigo == "PUBLISHED":
            return self._resultado(self._buscar_media(t), "Recuperado tras una interrupción.")
        if codigo == "IN_PROGRESS":
            return ESPERAR
        return REINTENTAR  # FINISHED sin publicar, ERROR o EXPIRED: no está publicado

    def _recuperar_story(self, contenedores: list[str]) -> Resultado | str:
        """Las Stories solo se publican cuando todos los contenedores existen: el estado de cada uno lo dice todo."""
        publicadas = sum(self._get(c, fields="status_code").get("status_code") == "PUBLISHED" for c in contenedores)
        if publicadas == 0:
            return REINTENTAR  # ninguna salió: los contenedores sin publicar caducan solos
        if publicadas == len(contenedores):
            return Resultado(P_PUBLICADO, PREFIJO_STORY + ",".join(contenedores), None, datetime.now(timezone.utc),
                             "Recuperado tras una interrupción.")
        raise ResultadoIncierto(f"Una ejecución se cortó a mitad de las Stories: salieron {publicadas} de "
                                f"{len(contenedores)} en Instagram. Sube a mano las que faltan.")

    def _buscar_media(self, t: Trabajo) -> str | None:
        recientes = self._get(f"{self.ig}/media", fields="id,caption,timestamp", limit=15).get("data", [])
        for m in recientes:
            if (m.get("caption") or "").strip() == t.texto.strip():
                return m["id"]
        return None
