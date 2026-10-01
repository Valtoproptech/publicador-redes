"""Credenciales OAuth de Google (Drive y YouTube) guardadas como JSON en los secretos."""
from __future__ import annotations

import json

ALCANCES_DRIVE = ["https://www.googleapis.com/auth/drive.readonly"]
ALCANCES_YOUTUBE = ["https://www.googleapis.com/auth/youtube.upload",
                    "https://www.googleapis.com/auth/youtube.readonly"]


def credenciales(json_secreto: str, alcances: list[str]):
    from google.oauth2.credentials import Credentials
    return Credentials.from_authorized_user_info(json.loads(json_secreto), alcances)


def autorizar(archivo_cliente: str, alcances: list[str]) -> str:
    """Abre el navegador para que el dueño de la cuenta autorice. Devuelve el JSON a guardar."""
    from google_auth_oauthlib.flow import InstalledAppFlow
    flujo = InstalledAppFlow.from_client_secrets_file(archivo_cliente, alcances)
    cred = flujo.run_local_server(port=0, access_type="offline", prompt="consent")
    return cred.to_json()
