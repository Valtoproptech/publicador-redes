"""Línea de comandos:  python -m publicador <comando>

  ejecutar [--simulacro]        una pasada (en GitHub corre sola cada 5 min)
  verificar                     comprueba config, Notion, Drive y cada cuenta conectada
  guardar-secreto NOMBRE        guarda un token (te lo pide sin mostrarlo)
  conectar-google NOMBRE --para drive|youtube   abre el navegador para autorizar
  conectar-tiktok NOMBRE        abre el navegador para autorizar una cuenta de TikTok
  descubrir-meta                lista tus páginas de Facebook e Instagram para config.toml
  subir-a-github                cifra los tokens y los sube, junto con config.toml, a GitHub
"""
from __future__ import annotations

import argparse
import getpass
import logging
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from . import secretos as S
from .config import cargar


def _notion_y_drive(sec):
    from .drive import Drive
    from .google_oauth import ALCANCES_DRIVE, credenciales
    from .notion import Notion
    return Notion(sec.leer("notion")), Drive(credenciales(sec.leer("google-drive"), ALCANCES_DRIVE))


def ejecutar(args) -> int:
    from .bloqueo import BloqueoArchivo
    from .motor import Medios, Motor
    from .redes import fabrica
    cfg = cargar()
    sec = S.crear()
    run = uuid.uuid4().hex[:8]
    bloqueo = None
    if not args.simulacro:
        bloqueo = BloqueoArchivo()
        if not bloqueo.adquirir(run):
            print("Otra ejecución sigue en curso; esta se omite.")
            return 0
    try:
        notion, drive = _notion_y_drive(sec)
        with tempfile.TemporaryDirectory(prefix="publicador-") as tmp:
            motor = Motor(cfg, notion, fabrica(cfg, sec), Medios(drive, Path(tmp)),
                          simulacro=args.simulacro, bloqueo=bloqueo, run=run)
            motor.ejecutar()
    finally:
        if bloqueo:
            bloqueo.liberar()
    return 0


def verificar(args) -> int:
    from .esquema import C, P
    from .redes import fabrica
    cfg = cargar()
    sec = S.crear()
    fallos = 0

    def chequeo(nombre, fn):
        nonlocal fallos
        try:
            print(f"✅ {nombre}: {fn()}")
        except Exception as e:
            fallos += 1
            print(f"❌ {nombre}: {e}")

    def base(id_, clase):
        props = notion.base(id_)["properties"]
        faltan = [v for k, v in vars(clase).items() if not k.startswith("_") and v not in props]
        if faltan:
            raise ValueError(f"faltan propiedades: {', '.join(faltan)}")
        return "estructura correcta"

    chequeo("ffmpeg", lambda: subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True).stdout.split("\n")[0])
    try:
        notion, drive = _notion_y_drive(sec)
    except Exception as e:
        print(f"❌ Credenciales básicas (notion / google-drive): {e}")
        return 1
    chequeo("Notion · Contenidos", lambda: base(cfg.notion_contenidos, C))
    chequeo("Notion · Publicaciones", lambda: base(cfg.notion_publicaciones, P))
    chequeo("Google Drive", drive.cuenta)
    crear = fabrica(cfg, sec)
    for cuenta, redes in cfg.cuentas.items():
        for red, d in redes.items():
            chequeo(f"{cuenta} · {red} ({d.via})", lambda c=cuenta, r=red: crear(c, r).comprobar())
    print("\nTodo listo." if not fallos else f"\n{fallos} problema(s). Revisa los ❌ de arriba.")
    return 1 if fallos else 0


def guardar_secreto(args) -> int:
    valor = getpass.getpass(f"Pega el valor de '{args.nombre}' y pulsa Enter (no se mostrará): ").strip()
    if not valor:
        print("Vacío; no se guardó nada.")
        return 1
    S.SecretosLocales().guardar(args.nombre, valor)
    print(f"Guardado '{args.nombre}'.")
    return 0


def conectar_google(args) -> int:
    from .google_oauth import ALCANCES_DRIVE, ALCANCES_YOUTUBE, autorizar
    alcances = ALCANCES_DRIVE if args.para == "drive" else ALCANCES_YOUTUBE
    print("Se abrirá el navegador. Entra con la cuenta de Google correcta"
          + (" y elige el canal de YouTube." if args.para == "youtube" else "."))
    S.SecretosLocales().guardar(args.nombre, autorizar(args.cliente, alcances))
    print(f"Conectado y guardado como '{args.nombre}'.")
    return 0


def conectar_tiktok(args) -> int:
    from .redes.tiktok import autorizar
    local = S.SecretosLocales()
    try:
        app = local.leer("tiktok-app").split("\n")
        client_key, client_secret = app[0].strip(), app[1].strip()
    except (KeyError, IndexError):
        client_key = input("Client key de tu app de TikTok: ").strip()
        client_secret = getpass.getpass("Client secret (no se mostrará): ").strip()
        local.guardar("tiktok-app", f"{client_key}\n{client_secret}")
    print("Se abrirá el navegador: entra con la cuenta de TikTok de esta marca y acepta.")
    local.guardar(args.nombre, autorizar(client_key, client_secret))
    print(f"Conectado y guardado como '{args.nombre}'.")
    return 0


def descubrir_meta(args) -> int:
    import requests
    cfg = cargar()
    token = S.crear().leer("meta")
    r = requests.get(f"https://graph.facebook.com/{cfg.graph_version}/me/accounts", timeout=30,
                     headers={"Authorization": f"OAuth {token}"},
                     params={"fields": "name,id,instagram_business_account{id,username}", "limit": 100}).json()
    if "error" in r:
        print("Error de Meta:", r["error"].get("message"))
        return 1
    print("# Copia en config.toml lo que corresponda (cambia el nombre de la cuenta si quieres):\n")
    for p in r.get("data", []):
        print(f'[cuentas."{p["name"]}".facebook]\nvia = "nativo"\npage_id = "{p["id"]}"\n')
        ig = p.get("instagram_business_account")
        if ig:
            print(f'[cuentas."{p["name"]}".instagram]  # @{ig.get("username")}\nvia = "nativo"\nig_user_id = "{ig["id"]}"\n')
    return 0


def subir_a_github(args) -> int:
    """Cifra los tokens locales en estado/secretos.cifrado y guarda clave + config como secrets de GitHub."""
    local = S.SecretosLocales()
    try:
        clave = local.leer("_clave")
    except KeyError:
        clave = S.nueva_clave()
        local.guardar("_clave", clave)
    subprocess.run(["git", "pull", "--quiet", "--rebase"], check=False)
    cifrados = S.SecretosCifrados(clave)
    for nombre in local.nombres():
        if nombre.startswith("_") or nombre == "tiktok-app":
            continue
        if nombre.startswith("tiktok-") and nombre in cifrados.datos and not args.forzar:
            continue  # GitHub ya renovó ese token: la copia de la nube es la más nueva
        cifrados.guardar(nombre, local.leer(nombre))
        print(f"🔒 {nombre}")
    cargar()  # valida config.toml antes de subirlo
    for nombre, valor in (("PUBLICADOR_CLAVE", clave), ("PUBLICADOR_CONFIG_TOML", Path("config.toml").read_text())):
        subprocess.run(["gh", "secret", "set", nombre], input=valor, text=True, check=True)
        print(f"🔑 secret de GitHub {nombre} actualizado")
    subprocess.run(["git", "add", str(S.ARCHIVO_CIFRADO)], check=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode:
        subprocess.run(["git", "commit", "-q", "-m", "estado: tokens actualizados"], check=True)
        subprocess.run(["git", "push", "-q"], check=True)
        print("⬆️  archivo cifrado subido")
    return 0


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    ap = argparse.ArgumentParser(prog="publicador", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("ejecutar")
    e.add_argument("--simulacro", action="store_true", help="revisa todo y descarga videos, pero no escribe ni publica")
    e.set_defaults(fn=ejecutar)
    sub.add_parser("verificar").set_defaults(fn=verificar)
    g = sub.add_parser("guardar-secreto")
    g.add_argument("nombre")
    g.set_defaults(fn=guardar_secreto)
    c = sub.add_parser("conectar-google")
    c.add_argument("nombre", help="ej. google-drive o youtube-franco")
    c.add_argument("--para", choices=["drive", "youtube"], required=True)
    c.add_argument("--cliente", default="credenciales/google-oauth-cliente.json")
    c.set_defaults(fn=conectar_google)
    t = sub.add_parser("conectar-tiktok")
    t.add_argument("nombre", help="ej. tiktok-bambu")
    t.set_defaults(fn=conectar_tiktok)
    sub.add_parser("descubrir-meta").set_defaults(fn=descubrir_meta)
    s = sub.add_parser("subir-a-github")
    s.add_argument("--forzar", action="store_true", help="sube también los tokens de TikTok aunque ya existan")
    s.set_defaults(fn=subir_a_github)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
