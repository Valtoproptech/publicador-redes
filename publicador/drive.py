"""Descarga el archivo ORIGINAL de Google Drive, byte a byte, y verifica su MD5."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable

from .errores import ErrorTransitorio, ErrorValidacion

TROZO = 32 * 1024 * 1024


class _EscritorConHash:
    def __init__(self, f):
        self.f, self.md5 = f, hashlib.md5()

    def write(self, datos: bytes) -> int:
        self.md5.update(datos)
        return self.f.write(datos)


class Drive:
    def __init__(self, credenciales):
        from googleapiclient.discovery import build
        self.api = build("drive", "v3", credentials=credenciales, cache_discovery=False)

    def cuenta(self) -> str:
        return self.api.about().get(fields="user(emailAddress)").execute()["user"]["emailAddress"]

    def metadatos(self, file_id: str) -> dict:
        from googleapiclient.errors import HttpError
        campos = "id,name,mimeType,size,md5Checksum,videoMediaMetadata,imageMediaMetadata,shortcutDetails"
        try:
            m = self.api.files().get(fileId=file_id, fields=campos, supportsAllDrives=True).execute()
        except HttpError as e:
            if e.resp.status in (403, 404):
                raise ErrorValidacion("No encuentro el archivo en Drive o la cuenta conectada no tiene acceso. "
                                      "Revisa el enlace y que el archivo esté compartido con esa cuenta.") from e
            raise ErrorTransitorio(f"Drive respondió {e.resp.status}") from e
        if m.get("mimeType") == "application/vnd.google-apps.shortcut":
            return self.metadatos(m["shortcutDetails"]["targetId"])
        return m

    def listar(self, carpeta_id: str) -> list[dict]:
        """Archivos de una carpeta (sin subcarpetas), con los atajos resueltos a su archivo real."""
        from googleapiclient.errors import HttpError
        campos = "nextPageToken,files(id,name,mimeType,size,md5Checksum,imageMediaMetadata,shortcutDetails)"
        try:
            m = self.api.files().get(fileId=carpeta_id, fields="id,mimeType", supportsAllDrives=True).execute()
            if m.get("mimeType") != "application/vnd.google-apps.folder":
                raise ErrorValidacion("El enlace de Drive no es una carpeta.")
            archivos, pagina = [], None
            while True:
                r = self.api.files().list(q=f"'{carpeta_id}' in parents and trashed=false", fields=campos,
                                          pageSize=100, pageToken=pagina, supportsAllDrives=True,
                                          includeItemsFromAllDrives=True).execute()
                archivos += r.get("files", [])
                pagina = r.get("nextPageToken")
                if not pagina:
                    break
        except HttpError as e:
            if e.resp.status in (403, 404):
                raise ErrorValidacion("No encuentro la carpeta en Drive o la cuenta conectada no tiene acceso. "
                                      "Revisa el enlace y que la carpeta esté compartida con esa cuenta.") from e
            raise ErrorTransitorio(f"Drive respondió {e.resp.status}") from e
        return [self.metadatos(a["id"]) if a.get("mimeType") == "application/vnd.google-apps.shortcut" else a
                for a in archivos]

    def descargar(self, file_id: str, carpeta: Path, *, tipo: str = "video/",
                  latido: Callable[[], None] = lambda: None) -> tuple[Path, dict]:
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaIoBaseDownload
        m = self.metadatos(file_id)
        if not m.get("mimeType", "").startswith(tipo):
            raise ErrorValidacion(f"El archivo de Drive '{m.get('name')}' no es {tipo.rstrip('/')} "
                                  f"(es {m.get('mimeType')}).")
        destino = carpeta / f"{m['id']}{Path(m.get('name', '')).suffix.lower()}"
        pedido = self.api.files().get_media(fileId=m["id"], supportsAllDrives=True)
        try:
            with open(destino, "wb") as f:
                escritor = _EscritorConHash(f)
                bajada = MediaIoBaseDownload(escritor, pedido, chunksize=TROZO)
                terminado = False
                while not terminado:
                    _, terminado = bajada.next_chunk(num_retries=3)
                    latido()
        except HttpError as e:
            raise ErrorTransitorio(f"Falló la descarga de Drive ({e.resp.status})") from e
        esperado = m.get("md5Checksum")
        if esperado and escritor.md5.hexdigest() != esperado:
            raise ErrorTransitorio("La descarga de Drive llegó corrupta (MD5 distinto); se reintentará.")
        m["md5_verificado"] = bool(esperado)
        return destino, m
