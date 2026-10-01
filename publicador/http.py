"""Llamadas HTTP a plataformas con clasificación de errores.

`final=True` marca la llamada que publica. Si esa falla sin respuesta clara, no sabemos si se
publicó: se lanza ResultadoIncierto para que NADIE reintente a ciegas.
"""
from __future__ import annotations

import requests

from .errores import ErrorPermanente, ErrorTransitorio, ResultadoIncierto

# Códigos de Meta que significan "límite temporal / reintenta luego".
_META_TRANSITORIOS = {1, 2, 4, 9, 17, 32, 341, 613, 80001, 80002}


def mensaje_error(r: requests.Response) -> str:
    try:
        j = r.json()
    except ValueError:
        return f"HTTP {r.status_code}: {r.text[:300]}"
    e = j.get("error", j)
    if isinstance(e, dict):
        partes = [e.get("error_user_msg") or e.get("message") or e.get("error_description"),
                  f"code={e['code']}" if "code" in e else None,
                  f"subcode={e['error_subcode']}" if "error_subcode" in e else None]
        texto = " ".join(p for p in partes if p)
        if texto:
            return f"HTTP {r.status_code}: {texto}"
    return f"HTTP {r.status_code}: {str(j)[:300]}"


def _transitorio(r: requests.Response) -> bool:
    if r.status_code == 429 or r.status_code >= 500:
        return True
    try:
        e = r.json().get("error", {})
        return isinstance(e, dict) and (e.get("is_transient") or e.get("code") in _META_TRANSITORIOS)
    except ValueError:
        return False


def llamar(sesion: requests.Session, metodo: str, url: str, *, contexto: str, final: bool = False,
           timeout=(20, 600), **kw) -> requests.Response:
    try:
        r = sesion.request(metodo, url, timeout=timeout, **kw)
    except (requests.ConnectionError, requests.Timeout) as e:
        if final:
            raise ResultadoIncierto(f"{contexto}: la plataforma no respondió ({type(e).__name__}). "
                                    "Puede que sí se haya publicado: revisa la red antes de reintentar.") from e
        raise ErrorTransitorio(f"{contexto}: error de red ({type(e).__name__})") from e
    if r.status_code < 400:
        return r
    msg = f"{contexto}: {mensaje_error(r)}"
    if final and r.status_code >= 500:
        raise ResultadoIncierto(msg + " — puede que sí se haya publicado: revisa la red antes de reintentar.")
    if _transitorio(r):
        raise ErrorTransitorio(msg)
    raise ErrorPermanente(msg)
