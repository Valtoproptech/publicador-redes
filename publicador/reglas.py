"""Reglas puras del publicador: sin red, sin disco, sin credenciales.

Todo lo que decide *qué* hacer vive aquí para poder probarlo de forma aislada.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .errores import ErrorValidacion

REDES = ("Instagram", "Facebook", "TikTok", "YouTube")

# Estados de la base "Contenidos" (los pone el equipo o el sistema).
C_BORRADOR = "Borrador"
C_LISTO = "Listo para publicar"
C_PROGRAMADO = "Programado"
C_PUBLICADO = "Publicado"
C_ATENCION = "Requiere atención"
C_ERROR = "Error"
C_CANCELADO = "Cancelado"

# Estados de la base "Publicaciones" (solo los pone el sistema, salvo los marcados).
P_EN_COLA = "En cola"
P_SUBIENDO = "Subiendo"
P_PROGRAMADO = "Programado"
P_PUBLICADO = "Publicado"            # también lo marca el equipo tras publicar un borrador
P_BORRADORES = "En borradores"
P_ERROR = "Error"
P_REVISION = "Requiere revisión"
P_CANCELADO = "Cancelado"

P_ACTIVOS = (P_EN_COLA, P_SUBIENDO, P_PROGRAMADO)
P_ATENCION = (P_ERROR, P_REVISION, P_BORRADORES)

# Tiempos de planificación.
MARGEN_NATIVO = timedelta(minutes=15)     # por debajo de esto se publica "a la hora" en vez de programar
MAX_NATIVO = {"Facebook": timedelta(days=28), "YouTube": timedelta(days=3650)}
MAX_RETRASO = timedelta(hours=2)          # si la fecha ya pasó hace más, no publicamos sin permiso
ESPERA_VERIFICACION = timedelta(minutes=5)
BLOQUEO_VENCIDO = timedelta(minutes=15)
MAX_INTENTOS = 3


# ---------------------------------------------------------------- Drive / fechas / texto

_DRIVE = [
    re.compile(r"drive\.google\.com/file/d/([A-Za-z0-9_-]{10,})"),
    re.compile(r"drive\.google\.com/(?:open|uc)\?(?:[^#]*&)?id=([A-Za-z0-9_-]{10,})"),
]


def id_de_drive(url: str | None) -> str:
    """Extrae el ID de archivo de un enlace de Google Drive."""
    if not url or not url.strip():
        raise ErrorValidacion("Falta el enlace del video en la propiedad URL.")
    if "/folders/" in url:
        raise ErrorValidacion("La URL es una carpeta de Drive; debe ser el enlace al archivo de video.")
    for patron in _DRIVE:
        m = patron.search(url)
        if m:
            return m.group(1)
    raise ErrorValidacion("La URL no es un enlace de archivo de Google Drive (drive.google.com/file/d/...).")


_CARPETA = re.compile(r"drive\.google\.com/(?:drive/(?:u/\d+/)?)?folders/([A-Za-z0-9_-]{10,})")


def enlace_drive(url: str | None) -> tuple[str, bool]:
    """(ID, es_carpeta). Una carpeta de Drive es un carrusel de fotos; un archivo, un video o una foto."""
    m = _CARPETA.search(url or "")
    if m:
        return m.group(1), True
    return id_de_drive(url), False


def orden_natural(nombre: str) -> list:
    """'2.jpg' antes que '10.jpg': así se ordenan las fotos de un carrusel."""
    return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", nombre.casefold())]


def parsear_fecha(valor: str | None, zona: str) -> datetime:
    """Convierte la fecha de Notion en un datetime con zona horaria. Exige hora."""
    if not valor:
        raise ErrorValidacion("Falta la fecha de publicación.")
    if "T" not in valor:
        raise ErrorValidacion("La fecha no tiene hora. En Notion activa 'Incluir hora' y ponla.")
    dt = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(zona))
    return dt


def normalizar_hashtags(texto: str | None) -> list[str]:
    """'inmobiliaria, #Lima  depas' -> ['#inmobiliaria', '#Lima', '#depas'] sin duplicados."""
    if not texto:
        return []
    vistos, salida = set(), []
    for palabra in re.split(r"[\s,;]+", texto):
        palabra = palabra.strip().lstrip("#")
        if palabra and palabra.lower() not in vistos:
            vistos.add(palabra.lower())
            salida.append("#" + palabra)
    return salida


def componer_texto(copy: str | None, hashtags: str | None) -> str:
    partes = [(copy or "").strip()]
    tags = normalizar_hashtags(hashtags)
    if tags:
        partes.append(" ".join(tags))
    return "\n\n".join(p for p in partes if p)


def limpiar_para_youtube(texto: str, maximo: int) -> str:
    """YouTube rechaza '<' y '>' en títulos y descripciones."""
    texto = texto.replace("<", "‹").replace(">", "›").strip()
    return texto[:maximo]


def validar_texto(red: str, texto: str) -> None:
    if red in ("Instagram", "TikTok") and len(texto) > 2200:
        raise ErrorValidacion(f"El copy + hashtags supera 2200 caracteres ({len(texto)}), límite de {red}.")
    if red == "Instagram" and len(re.findall(r"#\w", texto)) > 30:
        raise ErrorValidacion("Instagram admite máximo 30 hashtags.")


# ---------------------------------------------------------------- Calidad de video

@dataclass
class InfoVideo:
    formato: str            # ffprobe format_name, ej. "mov,mp4,m4a,3gp,3g2,mj2"
    codec_video: str
    ancho: int              # ya corregido por rotación (lo que se ve)
    alto: int
    fps: float
    duracion: float         # segundos
    bitrate_video: int      # bps
    peso: int               # bytes
    codec_audio: str | None = None
    muestreo_audio: int | None = None
    moov_al_inicio: bool = True

    def resumen(self) -> str:
        audio = f"{self.codec_audio} {self.muestreo_audio} Hz" if self.codec_audio else "sin audio"
        return (f"{self.ancho}x{self.alto} {self.codec_video} {self.fps:.2f}fps "
                f"{self.bitrate_video / 1e6:.1f}Mbps {self.duracion:.1f}s "
                f"{self.peso / 1e6:.1f}MB, {audio}")


MB = 1_000_000
LIMITES = {
    # Fuente: specs oficiales de cada API (ver docs/01-decisiones.md).
    "Instagram": dict(codecs={"h264", "hevc"}, ancho_max=1920, fps=(23, 60), bitrate_max=25_000_000,
                      peso_max=300 * MB, duracion=(3, 900), audio={"aac"}, muestreo_max=48_000),
    "Facebook": dict(codecs={"h264", "hevc"}, fps=(24, 60), duracion=(3, 14_400), lado_corto_min=540,
                     peso_max=4_000 * MB),
    "TikTok": dict(codecs={"h264", "hevc", "vp8", "vp9"}, lado_max=4096, lado_corto_min=360, fps=(23, 60),
                   peso_max=4_000 * MB, duracion=(3, 600)),
    "YouTube": dict(),
}


@dataclass
class Diagnostico:
    arreglable: list[str] = field(default_factory=list)      # se corrige recodificando
    solo_contenedor: list[str] = field(default_factory=list)  # se corrige sin tocar la imagen (remux)
    fatal: list[str] = field(default_factory=list)            # no se puede corregir automáticamente

    @property
    def ok(self) -> bool:
        return not (self.arreglable or self.solo_contenedor or self.fatal)


def diagnosticar(info: InfoVideo, red: str) -> Diagnostico:
    lim, d = LIMITES[red], Diagnostico()
    if not lim:
        return d
    lo, hi = lim.get("duracion", (0, None))
    if info.duracion < lo or (hi and info.duracion > hi):
        d.fatal.append(f"duración {info.duracion:.0f}s fuera del rango de {red} ({lo}-{hi}s)")
    corto = min(info.ancho, info.alto)
    if corto < lim.get("lado_corto_min", 0):
        d.fatal.append(f"resolución {info.ancho}x{info.alto} demasiado baja para {red}")
    if info.codec_video not in lim["codecs"]:
        d.arreglable.append(f"códec {info.codec_video} no admitido")
    if info.ancho > lim.get("ancho_max", 10**6) or max(info.ancho, info.alto) > lim.get("lado_max", 10**6):
        d.arreglable.append(f"resolución {info.ancho}x{info.alto} supera el máximo")
    fmin, fmax = lim.get("fps", (0, 1000))
    if not fmin <= round(info.fps) <= fmax:
        d.arreglable.append(f"{info.fps:.1f} fps fuera de {fmin}-{fmax}")
    if info.bitrate_video > lim.get("bitrate_max", 10**12):
        d.arreglable.append(f"bitrate {info.bitrate_video / 1e6:.1f} Mbps supera el máximo")
    if info.peso > lim.get("peso_max", 10**15):
        d.arreglable.append(f"peso {info.peso / MB:.0f} MB supera el máximo")
    if info.codec_audio and "audio" in lim and info.codec_audio not in lim["audio"]:
        d.arreglable.append(f"audio {info.codec_audio} no admitido")
    if info.muestreo_audio and info.muestreo_audio > lim.get("muestreo_max", 10**9):
        d.arreglable.append(f"audio a {info.muestreo_audio} Hz supera el máximo")
    if "mp4" not in info.formato and "mov" not in info.formato:
        d.solo_contenedor.append(f"contenedor {info.formato} (se pasa a MP4 sin recomprimir)")
    elif not info.moov_al_inicio:
        d.solo_contenedor.append("índice MP4 al final (se reordena sin recomprimir)")
    return d


# ---------------------------------------------------------------- Fotos y carruseles

REDES_FOTOS = ("Instagram", "Facebook")   # TikTok y YouTube no admiten fotos por API con apps gratis
MAX_FOTOS = 10                            # máximo de Instagram (y de este sistema) por carrusel
MIB = 1024 * 1024
FORMATOS_FOTO = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}
LIMITES_FOTO = {
    # Instagram solo acepta JPEG por URL pública (8 MB); la URL la da Notion, que en el plan gratis
    # acepta hasta 5 MiB por archivo. Proporción: de 4:5 (vertical) a 1.91:1 (horizontal).
    "Instagram": dict(formatos={"JPEG"}, peso_max=5 * MIB, proporcion=(0.8, 1.91)),
    # Facebook recibe el archivo directo; JPEG y PNG sin conversión.
    "Facebook": dict(formatos={"JPEG", "PNG"}, peso_max=10 * MB),
}


@dataclass
class InfoFoto:
    formato: str            # PIL: "JPEG", "PNG", "WEBP", "HEIF"...
    ancho: int              # ya corregido por la orientación EXIF (lo que se ve)
    alto: int
    peso: int
    orientacion: int = 1    # etiqueta EXIF 274; distinta de 1 = la imagen se gira al mostrarse
    modo: str = "RGB"

    def resumen(self) -> str:
        return f"{self.ancho}x{self.alto} {self.formato} {self.peso / 1e6:.1f}MB"


def proporcion_fuera(ancho: int, alto: int, red: str) -> str | None:
    """Motivo si la proporción no la admite la red (se valida con los metadatos de Drive, sin descargar)."""
    rango = LIMITES_FOTO.get(red, {}).get("proporcion")
    if not rango or not alto:
        return None
    p = ancho / alto
    if rango[0] - 0.005 <= p <= rango[1] + 0.005:
        return None
    return (f"mide {ancho}x{alto} y {red} solo acepta fotos entre 4:5 (vertical, ej. 1080x1350) y 1.91:1 "
            "(horizontal). Expórtala en esa proporción")


def diagnosticar_foto(info: InfoFoto, red: str) -> Diagnostico:
    """fatal = no se puede publicar así; arreglable = se convierte a JPEG de alta calidad."""
    lim, d = LIMITES_FOTO[red], Diagnostico()
    motivo = proporcion_fuera(info.ancho, info.alto, red)
    if motivo:
        d.fatal.append(motivo)
    if info.formato not in lim["formatos"]:
        d.arreglable.append(f"formato {info.formato}")
    if info.peso > lim["peso_max"]:
        d.arreglable.append(f"peso {info.peso / 1e6:.1f} MB")
    if info.orientacion not in (0, 1):
        d.arreglable.append("rotación EXIF")
    if info.modo not in ("RGB", "L") and info.formato == "JPEG":
        d.arreglable.append(f"color {info.modo}")
    return d


# ---------------------------------------------------------------- Planificación

@dataclass
class Plan:
    accion: str   # esperar | programar_nativo | publicar_a_la_hora | verificar | vencido | recuperar
    motivo: str = ""


def planificar(*, red: str, via: str, estado: str, cuando: datetime, ahora: datetime,
               anticipacion: timedelta, bloqueo_desde: datetime | None = None,
               bloqueo_propio: bool = False) -> Plan:
    faltan = cuando - ahora
    if estado == P_SUBIENDO:
        if bloqueo_propio:
            return Plan("esperar", "en curso en esta ejecución")
        if bloqueo_desde is None or ahora - bloqueo_desde > BLOQUEO_VENCIDO:
            return Plan("recuperar", "una ejecución anterior se interrumpió")
        return Plan("esperar", "otra ejecución la está procesando")
    if estado == P_PROGRAMADO:
        if faltan <= -ESPERA_VERIFICACION:
            return Plan("verificar")
        return Plan("esperar", "programada en la plataforma")
    if estado != P_EN_COLA:
        return Plan("esperar", f"estado {estado}")
    if faltan < -MAX_RETRASO:
        return Plan("vencido", f"la fecha pasó hace {_humano(-faltan)}")
    nativo = via == "nativo" and red in MAX_NATIVO
    if nativo and MARGEN_NATIVO < faltan <= MAX_NATIVO[red]:
        return Plan("programar_nativo")
    if faltan <= anticipacion:
        return Plan("publicar_a_la_hora")
    return Plan("esperar", f"faltan {_humano(faltan)}")


def _humano(delta: timedelta) -> str:
    minutos = int(delta.total_seconds() // 60)
    if minutos < 120:
        return f"{minutos} min"
    horas = minutos // 60
    return f"{horas} h" if horas < 48 else f"{horas // 24} días"


def estado_contenido(estados_publicaciones: list[str]) -> str:
    """Resume el estado de todas las publicaciones de un contenido."""
    if not estados_publicaciones:
        return C_PROGRAMADO
    if all(e == P_CANCELADO for e in estados_publicaciones):
        return C_CANCELADO
    vivos = [e for e in estados_publicaciones if e != P_CANCELADO]
    if any(e in P_ATENCION for e in vivos):
        return C_ATENCION
    if all(e == P_PUBLICADO for e in vivos):
        return C_PUBLICADO
    return C_PROGRAMADO


ICONOS = {P_PUBLICADO: "✅", P_PROGRAMADO: "🕒", P_EN_COLA: "🕒", P_SUBIENDO: "⏫", P_BORRADORES: "📝",
          P_ERROR: "❌", P_REVISION: "⚠️", P_CANCELADO: "🚫"}


def resumen_resultado(pubs: list[tuple[str, str]]) -> str:
    """[('Instagram','Publicado'), ('TikTok','Error')] -> 'Instagram ✅ · TikTok ❌'"""
    return " · ".join(f"{red} {ICONOS.get(est, est)}" for red, est in sorted(pubs))
