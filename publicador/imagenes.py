"""Analiza cada foto y, SOLO si la red lo exige, la convierte a JPEG de alta calidad.

Mismo criterio que los videos: original intacto siempre que se pueda. Se convierte cuando el
formato no lo admite la red (PNG, WEBP o HEIC en Instagram), si pesa demasiado o si depende de la
etiqueta de rotación EXIF. La proporción nunca se toca (recortar cambiaría la pieza): es un error.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from .errores import ErrorPermanente, ErrorValidacion
from .reglas import LIMITES_FOTO, InfoFoto, diagnosticar_foto

try:  # fotos HEIC de iPhone
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:  # pragma: no cover
    pass

ANCHO_MAX = 1440          # máximo que Instagram conserva; solo se aplica si de todos modos hay que convertir
CALIDADES = (95, 92, 90, 86, 82)


@dataclass
class Foto:
    ruta: Path
    nombre: str
    info: InfoFoto
    detalle: str
    md5: str
    url: str | None = None   # URL pública temporal (Notion) para las redes que la piden (Instagram)


@dataclass
class Secuencia:
    """Stories en orden: cada pieza es una Foto o un video (media.ArchivoPreparado)."""
    piezas: list

    @property
    def fotos(self) -> list[Foto]:
        return [p for p in self.piezas if isinstance(p, Foto)]

    @property
    def detalle(self) -> str:
        return " | ".join(f"{i}. {p.nombre or p.ruta.name}: {p.detalle}"
                          for i, p in enumerate(self.piezas, 1))


@dataclass
class Album:
    fotos: list[Foto]

    @property
    def detalle(self) -> str:
        return " | ".join(f"{i}. {f.nombre}: {f.detalle}" for i, f in enumerate(self.fotos, 1))


def _md5(ruta: Path) -> str:
    return hashlib.md5(ruta.read_bytes()).hexdigest()


def analizar_foto(ruta: Path) -> InfoFoto:
    from PIL import Image, UnidentifiedImageError
    try:
        with Image.open(ruta) as im:
            orientacion = int(im.getexif().get(274, 1) or 1)
            ancho, alto = im.size
            if orientacion in (5, 6, 7, 8):
                ancho, alto = alto, ancho
            # MPO = JPEG de iPhone con una imagen extra incrustada: para las redes es un JPEG normal
            formato = "JPEG" if im.format == "MPO" else (im.format or "")
            return InfoFoto(formato, ancho, alto, ruta.stat().st_size, orientacion, im.mode)
    except (UnidentifiedImageError, OSError) as e:
        raise ErrorValidacion(f"'{ruta.name}' no es una imagen legible ({e}).") from e


def _a_jpeg(origen: Path, destino: Path, red: str) -> None:
    from PIL import Image, ImageOps
    with Image.open(origen) as im:
        icc = im.info.get("icc_profile")
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            fondo = Image.new("RGB", im.size, (255, 255, 255))
            fondo.paste(im, mask=im.getchannel("A"))
            im = fondo
        elif im.mode != "RGB":
            im = im.convert("RGB")
        if red.startswith("Instagram") and im.width > ANCHO_MAX:
            im = im.resize((ANCHO_MAX, round(im.height * ANCHO_MAX / im.width)), Image.LANCZOS)
        extra = {"icc_profile": icc} if icc else {}
        for calidad in CALIDADES:
            im.save(destino, "JPEG", quality=calidad, subsampling=0, optimize=True, **extra)
            if destino.stat().st_size <= LIMITES_FOTO[red]["peso_max"]:
                return


def preparar_foto(origen: Path, nombre: str, red: str, carpeta: Path) -> Foto:
    info = analizar_foto(origen)
    diag = diagnosticar_foto(info, red)
    if diag.fatal:
        raise ErrorValidacion(f"La foto '{nombre}' " + "; ".join(diag.fatal) + ".")
    if diag.ok:
        return Foto(origen, nombre, info, f"Original intacto · {info.resumen()}", _md5(origen))
    destino = carpeta / f"{origen.stem}-{red.lower().replace(' ', '-')}.jpg"
    _a_jpeg(origen, destino, red)
    nuevo = analizar_foto(destino)
    restante = diagnosticar_foto(nuevo, red)
    if not restante.ok:
        raise ErrorPermanente(f"No logré adaptar la foto '{nombre}' a {red}: " +
                              "; ".join(restante.fatal + restante.arreglable))
    return Foto(destino, nombre, nuevo, f"Convertida a JPEG para {red} ({'; '.join(diag.arreglable)}). "
                                         f"Original: {info.resumen()} → Final: {nuevo.resumen()}", _md5(destino))
