"""Analiza el video con ffprobe y, SOLO si la red lo exige, lo adapta.

Orden de preferencia (de menos a más invasivo):
  1. Original intacto.
  2. Reordenar el contenedor (remux): misma imagen y audio, cero pérdida.
  3. Recodificar con calidad alta, solo para la red que lo necesita, y dejarlo registrado.
"""
from __future__ import annotations

import json
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .errores import ErrorPermanente, ErrorValidacion
from .reglas import LIMITES, InfoVideo, diagnosticar


@dataclass
class ArchivoPreparado:
    ruta: Path
    info: InfoVideo
    detalle: str


def _fps(texto: str | None) -> float:
    if not texto or texto == "0/0":
        return 0.0
    num, _, den = texto.partition("/")
    return float(num) / float(den or 1)


def moov_al_inicio(ruta: Path) -> bool:
    """En MP4/MOV el índice ('moov') debe ir antes de los datos ('mdat') para subir sin problemas."""
    with open(ruta, "rb") as f:
        while True:
            cabecera = f.read(8)
            if len(cabecera) < 8:
                return True
            tam, tipo = struct.unpack(">I4s", cabecera)
            if tam == 1:
                tam = struct.unpack(">Q", f.read(8))[0]
                f.seek(tam - 16, 1)
            elif tam == 0:
                return tipo != b"mdat"
            else:
                f.seek(tam - 8, 1)
            if tipo == b"moov":
                return True
            if tipo == b"mdat":
                return False


def analizar(ruta: Path) -> InfoVideo:
    salida = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(ruta)],
        capture_output=True, text=True)
    if salida.returncode != 0:
        raise ErrorValidacion(f"El archivo no es un video legible: {salida.stderr.strip()[:200]}")
    datos = json.loads(salida.stdout)
    video = next((s for s in datos["streams"] if s["codec_type"] == "video"
                  and not s.get("disposition", {}).get("attached_pic")), None)
    if video is None:
        raise ErrorValidacion("El archivo no contiene pista de video.")
    audio = next((s for s in datos["streams"] if s["codec_type"] == "audio"), None)
    formato = datos["format"]
    ancho, alto = int(video["width"]), int(video["height"])
    rotacion = int(video.get("tags", {}).get("rotate", 0) or 0)
    for lado in video.get("side_data_list", []):
        rotacion = int(lado.get("rotation", rotacion) or rotacion)
    if abs(rotacion) % 180 == 90:
        ancho, alto = alto, ancho
    duracion = float(formato.get("duration") or video.get("duration") or 0)
    peso = int(formato.get("size") or ruta.stat().st_size)
    bitrate = int(video.get("bit_rate") or 0)
    if not bitrate:
        total = int(formato.get("bit_rate") or (peso * 8 / duracion if duracion else 0))
        bitrate = total - int((audio or {}).get("bit_rate") or 0)
    es_mp4 = any(x in formato.get("format_name", "") for x in ("mp4", "mov"))
    return InfoVideo(
        formato=formato.get("format_name", ""), codec_video=video.get("codec_name", ""),
        ancho=ancho, alto=alto, fps=_fps(video.get("avg_frame_rate")) or _fps(video.get("r_frame_rate")),
        duracion=duracion, bitrate_video=bitrate, peso=peso,
        codec_audio=(audio or {}).get("codec_name"),
        muestreo_audio=int(audio["sample_rate"]) if audio and audio.get("sample_rate") else None,
        moov_al_inicio=moov_al_inicio(ruta) if es_mp4 else False,
    )


def _ffmpeg(args: list[str]) -> None:
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise ErrorPermanente(f"ffmpeg falló: {r.stderr.strip()[:300]}")


def _dimensiones(info: InfoVideo, red: str) -> tuple[int, int]:
    lim = LIMITES[red]
    factor = 1.0
    if red == "Instagram":
        # Si igual hay que recodificar, se entrega a la resolución que Instagram muestra (lado corto 1080),
        # reducida aquí con lanczos en vez de dejar que la plataforma la reduzca a su manera.
        factor = min(factor, 1080 / min(info.ancho, info.alto))
    if "ancho_max" in lim:
        factor = min(factor, lim["ancho_max"] / info.ancho)
    if "lado_max" in lim:
        factor = min(factor, lim["lado_max"] / max(info.ancho, info.alto))
    par = lambda x: max(2, int(x * factor) // 2 * 2)
    return par(info.ancho), par(info.alto)


def recodificar(origen: Path, info: InfoVideo, red: str, destino: Path) -> None:
    lim = LIMITES[red]
    ancho, alto = _dimensiones(info, red)
    maximo = 20_000_000
    if "peso_max" in lim and info.duracion:
        # que quepa en el peso máximo con 5 % de margen
        maximo = min(maximo, int(lim["peso_max"] * 8 * 0.95 / info.duracion) - 256_000)
    fmin, fmax = lim.get("fps", (0, 1000))
    args = ["-i", str(origen), "-map", "0:v:0", "-map", "0:a:0?",
            "-c:v", "libx264", "-preset", "slow", "-crf", "17", "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-maxrate", str(maximo), "-bufsize", str(maximo * 2),
            "-vf", f"scale={ancho}:{alto}:flags=lanczos"]
    if round(info.fps) > fmax:
        args += ["-r", str(fmax)]
    elif round(info.fps) < fmin:
        args += ["-r", "30"]
    args += ["-c:a", "aac", "-b:a", "128k" if red == "Instagram" else "192k", "-ar", "48000",
             "-movflags", "+faststart", str(destino)]
    _ffmpeg(args)


def preparar(origen: Path, red: str, carpeta: Path) -> ArchivoPreparado:
    info = analizar(origen)
    diag = diagnosticar(info, red)
    if diag.fatal:
        raise ErrorValidacion(f"El video no sirve para {red}: " + "; ".join(diag.fatal) + ".")
    if diag.ok:
        return ArchivoPreparado(origen, info, f"Original intacto · {info.resumen()}")
    destino = carpeta / f"{origen.stem}-{red.lower()}.mp4"
    if not diag.arreglable:
        _ffmpeg(["-i", str(origen), "-map", "0", "-c", "copy", "-movflags", "+faststart", str(destino)])
        motivo, accion = diag.solo_contenedor, "Reordenado sin recomprimir"
    else:
        recodificar(origen, info, red, destino)
        motivo, accion = diag.arreglable + diag.solo_contenedor, f"RECODIFICADO para {red}"
    nuevo = analizar(destino)
    restante = diagnosticar(nuevo, red)
    if not restante.ok:
        raise ErrorPermanente(f"No logré adaptar el video a {red}: " +
                              "; ".join(restante.fatal + restante.arreglable + restante.solo_contenedor))
    return ArchivoPreparado(destino, nuevo, f"{accion} ({'; '.join(motivo)}). "
                                            f"Original: {info.resumen()} → Final: {nuevo.resumen()}")
