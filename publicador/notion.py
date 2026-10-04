"""Cliente mínimo de la API REST de Notion + helpers para leer/escribir propiedades."""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import requests

from .errores import ErrorPermanente, ErrorTransitorio


class Notion:
    BASE = "https://api.notion.com/v1"
    VERSION = "2022-06-28"

    def __init__(self, token: str, sesion: requests.Session | None = None):
        self.s = sesion or requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}", "Notion-Version": self.VERSION,
                               "Content-Type": "application/json"})

    def _req(self, metodo: str, ruta: str, **kw) -> dict:
        for intento in range(5):
            try:
                r = self.s.request(metodo, self.BASE + ruta, timeout=30, **kw)
            except (requests.ConnectionError, requests.Timeout) as e:
                if intento == 4:
                    raise ErrorTransitorio(f"Notion no responde: {e}") from e
                time.sleep(2 ** intento)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                if intento == 4:
                    raise ErrorTransitorio(f"Notion {r.status_code}: {r.text[:200]}")
                time.sleep(float(r.headers.get("Retry-After", 2 ** intento)))
                continue
            if r.status_code >= 400:
                raise ErrorPermanente(f"Notion {r.status_code}: {r.json().get('message', r.text[:200])}")
            return r.json()
        raise AssertionError("inalcanzable")

    def consultar(self, base_id: str, filtro: dict | None = None) -> list[dict]:
        cuerpo: dict = {"page_size": 100}
        if filtro:
            cuerpo["filter"] = filtro
        paginas = []
        while True:
            r = self._req("POST", f"/databases/{base_id}/query", json=cuerpo)
            paginas += r["results"]
            if not r.get("has_more"):
                return paginas
            cuerpo["start_cursor"] = r["next_cursor"]

    def pagina(self, pagina_id: str) -> dict:
        return self._req("GET", f"/pages/{pagina_id}")

    def base(self, base_id: str) -> dict:
        return self._req("GET", f"/databases/{base_id}")

    def actualizar(self, pagina_id: str, propiedades: dict) -> dict:
        return self._req("PATCH", f"/pages/{pagina_id}", json={"properties": propiedades})

    def crear(self, base_id: str, propiedades: dict) -> dict:
        return self._req("POST", "/pages", json={"parent": {"database_id": base_id}, "properties": propiedades})

    # ------------------------------------------------------------ archivos (fotos de carruseles)

    def hijos(self, bloque_id: str) -> list[dict]:
        bloques, cursor = [], None
        while True:
            r = self._req("GET", f"/blocks/{bloque_id}/children",
                          params={"page_size": 100} | ({"start_cursor": cursor} if cursor else {}))
            bloques += r["results"]
            if not r.get("has_more"):
                return bloques
            cursor = r["next_cursor"]

    def agregar_imagen(self, pagina_id: str, ruta, nombre: str, leyenda: str) -> str:
        """Sube un archivo (máx. 5 MiB en el plan gratis) y lo agrega como imagen al cuerpo de la página.
        Devuelve la URL temporal (1 h) con la que cualquiera puede descargarlo."""
        tipo = "image/png" if str(ruta).lower().endswith(".png") else "image/jpeg"
        subida = self._req("POST", "/file_uploads", json={"filename": nombre, "content_type": tipo})
        datos = Path(ruta).read_bytes()  # en memoria (≤ 5 MiB): así un reintento reenvía el archivo completo
        # Content-Type None: que requests arme el multipart en vez de usar el JSON de la sesión
        self._req("POST", f"/file_uploads/{subida['id']}/send", files={"file": (nombre, datos, tipo)},
                  headers={"Content-Type": None})
        r = self._req("PATCH", f"/blocks/{pagina_id}/children", json={"children": [{"type": "image", "image": {
            "type": "file_upload", "file_upload": {"id": subida["id"]},
            "caption": [{"type": "text", "text": {"content": leyenda}}]}}]})
        return r["results"][0]["image"]["file"]["url"]


# ---------------------------------------------------------------- lectura

def leer(pagina: dict, nombre: str):
    """Devuelve el valor 'plano' de una propiedad, sea cual sea su tipo."""
    p = pagina.get("properties", {}).get(nombre)
    if p is None:
        return None
    for tipo in ("title", "rich_text"):
        if tipo in p:
            return "".join(t.get("plain_text") or t.get("text", {}).get("content", "") for t in p[tipo]).strip()
    if "select" in p:
        return (p["select"] or {}).get("name")
    if "status" in p:
        return (p["status"] or {}).get("name")
    if "multi_select" in p:
        return [o["name"] for o in p["multi_select"]]
    if "date" in p:
        return (p["date"] or {}).get("start")
    if "url" in p:
        return p["url"]
    if "number" in p:
        return p["number"]
    if "relation" in p:
        return [r["id"] for r in p["relation"]]
    if "files" in p:  # URLs temporales (1 h) de los archivos subidos a Notion
        return [(f.get("file") or f.get("external") or {}).get("url") for f in p["files"]]
    return None


# ---------------------------------------------------------------- escritura

def _trozos(texto: str) -> list[dict]:
    texto = texto or ""
    return [{"type": "text", "text": {"content": texto[i:i + 2000]}} for i in range(0, min(len(texto), 200_000), 2000)]


def titulo(texto: str) -> dict:
    return {"title": _trozos(texto)}


def texto(valor: str | None) -> dict:
    return {"rich_text": _trozos(valor or "")}


def opcion(nombre: str | None) -> dict:
    return {"select": {"name": nombre} if nombre else None}


def fecha(dt: datetime | None) -> dict:
    return {"date": {"start": dt.isoformat()} if dt else None}


def enlace(url: str | None) -> dict:
    return {"url": url or None}


def numero(n: float | None) -> dict:
    return {"number": n}


def relacion(ids: list[str]) -> dict:
    return {"relation": [{"id": i} for i in ids]}
