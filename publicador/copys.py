"""Copy automático: qué videos esperan copy y cómo se guarda.

El copy lo escribe Claude desde la app de Claude en la Mac de Franco (tarea programada "copys-redes"):
mira el video con el skill watch-video y escribe con el skill de estilo de la cuenta (skills/<nombre>/).
GitHub no escribe copys: solo espera a que exista antes de programar.

    [copy_automatico]
    "Franco Soler" = "copy-redes-franco"

Para sumar LinkedIn (u otra red): su skill de estilo y la red en REDES_COPY.
"""
from __future__ import annotations

from . import notion as N
from .esquema import C
from .reglas import MODO_CREAR, MODO_PROPIO, enlace_drive

REDES_COPY = ("Instagram", "TikTok")   # en orden de preferencia cuando un contenido va a varias


def red_del_copy(redes: list[str]) -> str | None:
    """La red cuyo estilo se usa. El copy es uno solo por contenido: si va a varias, manda la primera."""
    return next((r for r in REDES_COPY if r in redes), None)


def copy_pendiente(pag: dict, estilos: dict[str, str]) -> str | None:
    """None si el copy no es asunto del sistema (ya hay copy, o es "Copy propio"); "crear" si le toca a Claude;
    si no se puede, el motivo para mostrar en Notion."""
    if (N.leer(pag, C.COPY) or "").strip():
        return None
    modo = N.leer(pag, C.MODO_COPY)
    automatico = (N.leer(pag, C.CUENTA) or "") in estilos
    if modo == MODO_PROPIO or (not modo and not automatico):
        return None
    no_video = enlace_drive(N.leer(pag, C.URL))[1] or (N.leer(pag, C.FORMATO) or "Video") != "Video"
    if not automatico or no_video or red_del_copy(N.leer(pag, C.REDES) or []) is None:
        return (f"«{MODO_CREAR}» solo funciona con videos para Instagram o TikTok de las cuentas con copy "
                f"automático. Escribe el copy y elige «{MODO_PROPIO}».")
    return "crear"


def pendientes(notion, cfg) -> list[dict]:
    """Videos en Borrador o Listo que esperan su copy, con lo necesario para escribirlo."""
    filtro = {"or": [{"property": C.ESTADO, "select": {"equals": e}} for e in ("Borrador", "Listo para publicar")]}
    salida = []
    for pag in notion.consultar(cfg.notion_contenidos, filtro):
        if copy_pendiente(pag, cfg.copy_automatico) != "crear" or not N.leer(pag, C.URL):
            continue
        cuenta, redes = N.leer(pag, C.CUENTA), N.leer(pag, C.REDES) or []
        salida.append({"id": pag["id"], "titulo": N.leer(pag, C.TITULO), "cuenta": cuenta,
                       "red": red_del_copy(redes), "skill": cfg.copy_automatico[cuenta],
                       "estado": N.leer(pag, C.ESTADO), "fecha": N.leer(pag, C.FECHA),
                       "drive_id": enlace_drive(N.leer(pag, C.URL))[0],
                       "hashtags_puestos": bool((N.leer(pag, C.HASHTAGS) or "").strip())})
    return sorted(salida, key=lambda x: x["fecha"] or "9999")


def guardar(notion, pagina_id: str, copy: str, hashtags: str) -> None:
    """Escribe el copy; los hashtags solo si la fila no tenía. Nunca pisa un copy ya escrito."""
    pag = notion.pagina(pagina_id)
    if (N.leer(pag, C.COPY) or "").strip():
        raise ValueError("Esa fila ya tiene copy: no se toca.")
    props = {C.COPY: N.texto(copy.strip())}
    if not (N.leer(pag, C.HASHTAGS) or "").strip() and hashtags.strip():
        tags = ["#" + t.lstrip("#") for t in hashtags.replace(",", " ").split() if t.lstrip("#")]
        props[C.HASHTAGS] = N.texto(" ".join(tags[:5]))
    notion.actualizar(pagina_id, props)
