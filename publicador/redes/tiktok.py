"""TikTok con app propia (gratis): envía el video ORIGINAL a los borradores de la cuenta.

TikTok no deja que una app de uso interno publique sola en público (lo dice su guía de auditoría),
así que el último toque lo da una persona: abre la notificación de TikTok y publica.
Límite de TikTok: máximo 5 borradores pendientes cada 24 h por cuenta.
"""
from __future__ import annotations

import hashlib
import http.server
import json
import secrets as aleatorio
import time
import urllib.parse
import webbrowser

import requests

from ..errores import ErrorPermanente, ErrorTransitorio
from ..http import llamar
from ..reglas import P_BORRADORES, P_PUBLICADO, P_SUBIENDO
from ..secretos import enmascarar
from .base import ESPERAR, REINTENTAR, Adaptador, Resultado, Trabajo

API = "https://open.tiktokapis.com/v2"
AUTORIZAR = "https://www.tiktok.com/v2/auth/authorize/"
ALCANCES = "user.info.basic,video.upload"
PUERTO = 8765
REDIRECCION = f"http://localhost:{PUERTO}/callback/"
MB = 1024 * 1024
TROZO = 32 * MB   # TikTok exige trozos de 5–64 MB; el último absorbe el resto (hasta 128 MB)


def plan_trozos(tamano: int) -> tuple[int, int]:
    """(tamaño de trozo, cantidad de trozos) según las reglas de TikTok."""
    if tamano < 5 * MB or tamano <= TROZO:
        return tamano, 1
    return TROZO, tamano // TROZO


class TikTok(Adaptador):
    def __init__(self, nombre_secreto: str, secretos, sesion=None, dormir=time.sleep, espera_max: int = 600):
        self.nombre, self.secretos = nombre_secreto, secretos
        self.cred = json.loads(secretos.leer(nombre_secreto))
        self.s = sesion or requests.Session()
        self.dormir, self.espera_max = dormir, espera_max

    # ------------------------------------------------------------ autenticación

    def _token(self) -> str:
        if self.cred.get("expira", 0) - 300 > time.time():
            return self.cred["access_token"]
        r = llamar(self.s, "POST", f"{API}/oauth/token/", contexto="TikTok: renovar acceso", data={
            "client_key": self.cred["client_key"], "client_secret": self.cred["client_secret"],
            "grant_type": "refresh_token", "refresh_token": self.cred["refresh_token"]}).json()
        if "access_token" not in r:
            raise ErrorPermanente("TikTok: la autorización venció o fue revocada. Vuelve a conectar la cuenta "
                                  f"con: python -m publicador conectar-tiktok {self.nombre}")
        enmascarar(r["access_token"])
        self.cred |= {"access_token": r["access_token"], "refresh_token": r.get("refresh_token", self.cred["refresh_token"]),
                      "expira": int(time.time()) + int(r.get("expires_in", 86400))}
        self.secretos.guardar(self.nombre, json.dumps(self.cred))
        return self.cred["access_token"]

    def _post(self, ruta: str, cuerpo: dict, contexto: str) -> dict:
        r = llamar(self.s, "POST", API + ruta, json=cuerpo, contexto=contexto,
                   headers={"Authorization": f"Bearer {self._token()}"}).json()
        error = r.get("error") or {}
        if error.get("code", "ok") != "ok":
            if "rate_limit" in error.get("code", ""):
                raise ErrorTransitorio(f"{contexto}: {error.get('message')}")
            if "pending_share" in error.get("code", ""):
                raise ErrorPermanente("TikTok permite máximo 5 borradores pendientes al día por cuenta. "
                                      "Publica o borra los pendientes y vuelve a intentarlo.")
            raise ErrorPermanente(f"{contexto}: {error.get('message') or error.get('code')}")
        return r.get("data") or {}

    def comprobar(self) -> str:
        r = llamar(self.s, "GET", f"{API}/user/info/", params={"fields": "display_name"}, contexto="TikTok",
                   headers={"Authorization": f"Bearer {self._token()}"}).json()
        return r.get("data", {}).get("user", {}).get("display_name", "?") + " (modo borradores)"

    # ------------------------------------------------------------ publicar

    def publicar(self, t: Trabajo, video, portada, esperar, guardar_id) -> Resultado:
        esperar()
        tamano = video.ruta.stat().st_size
        trozo, cantidad = plan_trozos(tamano)
        datos = self._post("/post/publish/inbox/video/init/", {"source_info": {
            "source": "FILE_UPLOAD", "video_size": tamano, "chunk_size": trozo, "total_chunk_count": cantidad}},
            "TikTok: iniciar subida")
        publish_id = datos["publish_id"]
        guardar_id(publish_id)
        tipo = "video/quicktime" if video.ruta.suffix.lower() == ".mov" else "video/mp4"
        with open(video.ruta, "rb") as f:
            for i in range(cantidad):
                inicio = i * trozo
                fin = tamano - 1 if i == cantidad - 1 else inicio + trozo - 1
                parte = f.read(fin - inicio + 1)
                llamar(self.s, "PUT", datos["upload_url"], data=parte, contexto=f"TikTok: subir parte {i + 1}/{cantidad}",
                       headers={"Content-Type": tipo, "Content-Range": f"bytes {inicio}-{fin}/{tamano}"})
        return self._seguir(publish_id)

    def _estado(self, publish_id: str) -> Resultado | None:
        d = self._post("/post/publish/status/fetch/", {"publish_id": publish_id}, "TikTok: estado")
        estado = d.get("status", "")
        if estado == "SEND_TO_USER_INBOX":
            return Resultado(P_BORRADORES, publish_id, nota="Está en los borradores de TikTok: abre la notificación "
                                                            "en la app, publícalo y marca esta fila como Publicado.")
        if estado == "PUBLISH_COMPLETE":
            return Resultado(P_PUBLICADO, publish_id)
        if estado == "FAILED":
            raise ErrorPermanente(f"TikTok rechazó el video: {d.get('fail_reason', 'sin motivo')}")
        return None

    def _seguir(self, publish_id: str) -> Resultado:
        inicio = time.monotonic()
        while time.monotonic() - inicio < self.espera_max:
            res = self._estado(publish_id)
            if res:
                return res
            self.dormir(10)
        return Resultado(P_SUBIENDO, nota="TikTok sigue procesando; se revisará en la próxima ejecución.")

    def recuperar(self, t: Trabajo) -> Resultado | str:
        if not t.id_subida:
            return REINTENTAR  # como mucho quedaría un borrador duplicado, nunca una publicación doble
        try:
            return self._estado(t.id_subida) or ESPERAR
        except ErrorPermanente:
            return REINTENTAR


# ---------------------------------------------------------------- conexión inicial (una vez por cuenta)

def autorizar(client_key: str, client_secret: str) -> str:
    """Abre el navegador para autorizar la cuenta de TikTok (PKCE de escritorio). Devuelve el JSON a guardar."""
    verificador = aleatorio.token_urlsafe(48)
    estado = aleatorio.token_urlsafe(16)
    url = AUTORIZAR + "?" + urllib.parse.urlencode({
        "client_key": client_key, "scope": ALCANCES, "response_type": "code", "redirect_uri": REDIRECCION,
        "state": estado, "code_challenge": hashlib.sha256(verificador.encode()).hexdigest(),
        "code_challenge_method": "S256"})
    recibido: dict = {}

    class Receptor(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            recibido.update(urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h2>Listo, ya puedes cerrar esta pestaña.</h2>".encode())

        def log_message(self, *args):
            pass

    servidor = http.server.HTTPServer(("localhost", PUERTO), Receptor)
    webbrowser.open(url)
    while "code" not in recibido and "error" not in recibido:
        servidor.handle_request()
    servidor.server_close()
    if recibido.get("state", [""])[0] != estado or "code" not in recibido:
        raise RuntimeError(f"TikTok no autorizó: {recibido.get('error_description') or recibido.get('error')}")
    r = requests.post(f"{API}/oauth/token/", timeout=30, data={
        "client_key": client_key, "client_secret": client_secret, "code": recibido["code"][0],
        "grant_type": "authorization_code", "redirect_uri": REDIRECCION, "code_verifier": verificador}).json()
    if "access_token" not in r:
        raise RuntimeError(f"TikTok no entregó el token: {r}")
    return json.dumps({"client_key": client_key, "client_secret": client_secret, "open_id": r.get("open_id"),
                       "access_token": r["access_token"], "refresh_token": r["refresh_token"],
                       "expira": int(time.time()) + int(r.get("expires_in", 86400))})
