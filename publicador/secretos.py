"""Almacén de tokens.

- En tu Mac: carpeta .secretos/ (un archivo por token, ignorada por git).
- En GitHub Actions: archivo cifrado estado/secretos.cifrado dentro del repositorio. La clave para
  descifrarlo vive solo en los "secrets" de GitHub (PUBLICADOR_CLAVE). Cuando un token se renueva
  (TikTok), el archivo se vuelve a cifrar y el workflow lo guarda con un commit.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

# Nombres usados por el sistema:
#   notion         token de la integración interna de Notion
#   google-drive   OAuth de la cuenta de Google que ve los videos (JSON)
#   meta           token de usuario del sistema de Meta Business (no vence)
#   uploadpost     API key de Upload-Post (solo si se usa como respaldo)
#   youtube-<x>    OAuth de cada canal de YouTube (JSON)
#   tiktok-<x>     OAuth de cada cuenta de TikTok (JSON, se renueva solo)

ARCHIVO_CIFRADO = Path("estado/secretos.cifrado")


def enmascarar(valor: str) -> None:
    """En GitHub Actions, oculta el valor en los registros públicos (y cada campo si es JSON)."""
    if os.environ.get("GITHUB_ACTIONS") != "true" or not valor:
        return
    valores = [valor]
    try:
        datos = json.loads(valor)
        if isinstance(datos, dict):
            valores += [v for v in datos.values() if isinstance(v, str) and len(v) >= 8]
    except ValueError:
        pass
    for v in valores:
        for linea in v.splitlines():
            if linea.strip():
                print(f"::add-mask::{linea.strip()}", flush=True)


class SecretosLocales:
    def __init__(self, carpeta: str | Path = ".secretos"):
        self.carpeta = Path(carpeta)

    def leer(self, nombre: str) -> str:
        ruta = self.carpeta / nombre
        if not ruta.exists():
            raise KeyError(f"No existe el secreto '{nombre}'. Guárdalo con: python -m publicador guardar-secreto {nombre}")
        return ruta.read_text(encoding="utf-8").strip()

    def guardar(self, nombre: str, valor: str) -> None:
        self.carpeta.mkdir(mode=0o700, exist_ok=True)
        ruta = self.carpeta / nombre
        ruta.write_text(valor, encoding="utf-8")
        ruta.chmod(0o600)

    def nombres(self) -> list[str]:
        return sorted(p.name for p in self.carpeta.glob("*")) if self.carpeta.exists() else []


class SecretosCifrados:
    def __init__(self, clave: str, ruta: str | Path = ARCHIVO_CIFRADO):
        from cryptography.fernet import Fernet
        self.fernet, self.ruta = Fernet(clave.encode()), Path(ruta)
        self.datos: dict[str, str] = {}
        if self.ruta.exists():
            self.datos = json.loads(self.fernet.decrypt(self.ruta.read_bytes()))

    def leer(self, nombre: str) -> str:
        if nombre not in self.datos:
            raise KeyError(f"El secreto '{nombre}' no está en {self.ruta}. Súbelo con: python -m publicador cifrar-secretos")
        enmascarar(self.datos[nombre])
        return self.datos[nombre]

    def guardar(self, nombre: str, valor: str) -> None:
        self.datos[nombre] = valor
        self.ruta.parent.mkdir(exist_ok=True)
        self.ruta.write_bytes(self.fernet.encrypt(json.dumps(self.datos, sort_keys=True).encode()))

    def nombres(self) -> list[str]:
        return sorted(self.datos)


def nueva_clave() -> str:
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


def crear():
    clave = os.environ.get("PUBLICADOR_CLAVE")
    return SecretosCifrados(clave) if clave else SecretosLocales()
