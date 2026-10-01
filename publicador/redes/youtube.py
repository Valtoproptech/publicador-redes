"""YouTube vía Data API v3: subida resumable del original + programación nativa (publishAt)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..errores import ErrorPermanente, ErrorTransitorio, ResultadoIncierto
from ..reglas import P_PROGRAMADO, P_PUBLICADO, limpiar_para_youtube
from .base import REINTENTAR, Adaptador, Resultado, Trabajo

TROZO = 16 * 1024 * 1024
AVISO_AUDITORIA = ("YouTube dejó el video en PRIVADO. Pasa cuando el proyecto de Google aún no aprobó la "
                   "auditoría de la API. Mientras tanto, pon via = 'uploadpost' para YouTube en config.toml.")


def _rfc3339(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class YouTube(Adaptador):
    programa_nativo = True
    pista_sin_confirmar = AVISO_AUDITORIA

    def __init__(self, credenciales, latido=lambda: None):
        from googleapiclient.discovery import build
        self.api = build("youtube", "v3", credentials=credenciales, cache_discovery=False)
        self.latido = latido

    def comprobar(self) -> str:
        items = self.api.channels().list(part="snippet", mine=True).execute().get("items", [])
        if not items:
            raise ErrorPermanente("La cuenta de Google autorizada no tiene canal de YouTube.")
        return items[0]["snippet"]["title"]

    @staticmethod
    def _url(video_id: str, video) -> str:
        corto = video.info.duracion <= 180 and video.info.alto >= video.info.ancho
        return f"https://youtube.com/shorts/{video_id}" if corto else f"https://youtu.be/{video_id}"

    def _insertar(self, t: Trabajo, video, publicar_en: datetime | None) -> str:
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload
        estado = {"privacyStatus": "private" if publicar_en else "public", "selfDeclaredMadeForKids": False}
        if publicar_en:
            estado["publishAt"] = _rfc3339(publicar_en)
        cuerpo = {"snippet": {"title": limpiar_para_youtube(t.titulo_youtube or t.titulo, 100),
                              "description": limpiar_para_youtube(t.texto, 4900), "categoryId": "22"},
                  "status": estado}
        medio = MediaFileUpload(str(video.ruta), chunksize=TROZO, resumable=True, mimetype="video/*")
        pedido = self.api.videos().insert(part="snippet,status", body=cuerpo, media_body=medio)
        respuesta = None
        try:
            while respuesta is None:
                _, respuesta = pedido.next_chunk(num_retries=3)
                self.latido()
        except (HttpError, OSError) as e:
            enviado = getattr(pedido, "resumable_progress", 0) or 0
            if enviado >= video.ruta.stat().st_size:
                raise ResultadoIncierto(f"YouTube: falló la confirmación final ({e}). Revisa el canal.") from e
            if isinstance(e, HttpError) and e.resp.status < 500 and e.resp.status != 429:
                raise ErrorPermanente(f"YouTube rechazó el video: {e}") from e
            raise ErrorTransitorio(f"YouTube: subida interrumpida ({e})") from e
        return respuesta["id"]

    def _miniatura(self, video_id: str, portada: Path | None) -> str:
        if not portada:
            return ""
        from googleapiclient.http import MediaFileUpload
        try:
            self.api.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(portada))).execute()
            return ""
        except Exception as e:
            return f"Miniatura no aplicada (el canal debe estar verificado por teléfono): {e}"

    def programar(self, t, video, portada, guardar_id) -> Resultado:
        vid = self._insertar(t, video, t.cuando)
        return Resultado(P_PROGRAMADO, vid, self._url(vid, video), nota=self._miniatura(vid, portada))

    def publicar(self, t, video, portada, esperar, guardar_id) -> Resultado:
        # Si aún falta un poco, se sube ya como privado con publishAt (YouTube lo publica solo);
        # así la subida no retrasa la hora de publicación.
        if t.cuando - datetime.now(timezone.utc) > timedelta(minutes=2):
            return self.programar(t, video, portada, guardar_id)
        esperar()
        vid = self._insertar(t, video, None)
        return Resultado(P_PUBLICADO, vid, self._url(vid, video), datetime.now(timezone.utc),
                         self._miniatura(vid, portada))

    def _estado(self, video_id: str) -> dict | None:
        items = self.api.videos().list(part="status", id=video_id).execute().get("items", [])
        return items[0]["status"] if items else None

    def verificar(self, t: Trabajo) -> Resultado | None:
        e = self._estado(t.id_publicacion)
        if e is None:
            raise ErrorPermanente("El video ya no existe en YouTube (¿lo borraron?).")
        if e.get("uploadStatus") in ("rejected", "failed"):
            raise ErrorPermanente(f"YouTube rechazó el video: {e.get('rejectionReason') or e.get('failureReason')}")
        if e.get("privacyStatus") == "public":
            return Resultado(P_PUBLICADO, t.id_publicacion, None, datetime.now(timezone.utc))
        if not e.get("publishAt"):
            raise ErrorPermanente(AVISO_AUDITORIA)
        return None

    def recuperar(self, t: Trabajo) -> Resultado | str:
        """La subida no deja ID hasta terminar; buscamos entre las últimas subidas del canal por título."""
        canal = self.api.channels().list(part="contentDetails", mine=True).execute()["items"][0]
        lista = canal["contentDetails"]["relatedPlaylists"]["uploads"]
        items = self.api.playlistItems().list(part="snippet", playlistId=lista, maxResults=15).execute()["items"]
        titulo = limpiar_para_youtube(t.titulo_youtube or t.titulo, 100)
        desde = (t.bloqueo_desde or datetime.now(timezone.utc)) - timedelta(minutes=5)
        for it in items:
            sn = it["snippet"]
            subido = datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00"))
            if sn["title"] == titulo and subido >= desde:
                vid = sn["resourceId"]["videoId"]
                return Resultado(P_PROGRAMADO, vid, f"https://youtu.be/{vid}", nota="Recuperado tras una interrupción.")
        return REINTENTAR

    def reprogramar(self, t: Trabajo, cuando: datetime) -> None:
        self.api.videos().update(part="status", body={
            "id": t.id_publicacion,
            "status": {"privacyStatus": "private", "publishAt": _rfc3339(cuando), "selfDeclaredMadeForKids": False},
        }).execute()
