"""Motor: una ejecución = una pasada por Notion (cada 5 min en la nube).

1. Contenidos en "Listo para publicar" → se validan y se crea una fila por red en Publicaciones.
2. Cada publicación activa se planifica: esperar, programar en la plataforma, publicar a la hora,
   verificar o recuperar tras un corte.
3. El estado de cada contenido se recalcula a partir de sus publicaciones.

Anti-duplicados (en orden): un solo proceso a la vez (bloqueo global) → la fila pasa a "Subiendo"
y se relee antes de tocar ninguna plataforma → una fila con "ID publicación" nunca se vuelve a
publicar → los IDs intermedios se guardan al instante para conciliar → si no sabemos si se publicó,
"Requiere revisión" y nunca se reintenta solo.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import notion as N
from .errores import ErrorPermanente, ErrorPublicador, ErrorTransitorio, ErrorValidacion, ResultadoIncierto
from .esquema import C, P
from .imagenes import Album, preparar_foto
from .media import preparar
from .reglas import (C_ATENCION, C_BORRADOR, C_CANCELADO, C_ERROR, C_LISTO, C_PROGRAMADO, FORMATOS_FOTO,
                     MAX_FOTOS, MAX_INTENTOS, MAX_RETRASO, P_ACTIVOS, P_ATENCION, P_BORRADORES, P_CANCELADO,
                     P_EN_COLA, P_ERROR, P_PROGRAMADO, P_PUBLICADO, P_REVISION, P_SUBIENDO, REDES_FOTOS,
                     componer_texto, enlace_drive, estado_contenido, orden_natural, parsear_fecha, planificar,
                     proporcion_fuera, resumen_resultado, validar_texto)
from .redes.base import ESPERAR, REINTENTAR, Resultado, Trabajo

log = logging.getLogger("publicador")


# ---------------------------------------------------------------- modelos leídos de Notion

@dataclass
class Contenido:
    id: str
    titulo: str
    cuenta: str
    redes: list[str]
    cuando: datetime
    video_id: str
    portada_url: str | None
    texto: str
    titulo_youtube: str
    colaboradores: list[str]
    carpeta: bool = False      # la URL es una carpeta de Drive: fotos / carrusel


@dataclass
class Fila:
    id: str
    contenido_id: str | None
    red: str
    cuenta: str
    estado: str
    cuando: datetime | None
    via: str
    id_pub: str
    id_subida: str
    intentos: int
    bloqueo: str
    error: str

    def _campo(self, clave: str) -> str:
        m = re.search(rf"{clave}=([^|]*)", self.bloqueo or "")
        return m.group(1) if m else ""

    @property
    def bloqueo_run(self) -> str:
        return self._campo("run")

    @property
    def bloqueo_etapa(self) -> str:
        return self._campo("etapa")

    @property
    def bloqueo_desde(self) -> datetime | None:
        v = self._campo("desde")
        return datetime.fromisoformat(v) if v else None


def _fecha_o_none(valor: str | None) -> datetime | None:
    if not valor or "T" not in valor:
        return None
    dt = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def leer_fila(pag: dict) -> Fila:
    rel = N.leer(pag, P.CONTENIDO) or []
    return Fila(
        id=pag["id"], contenido_id=rel[0] if rel else None, red=N.leer(pag, P.RED) or "",
        cuenta=N.leer(pag, P.CUENTA) or "", estado=N.leer(pag, P.ESTADO) or "",
        cuando=_fecha_o_none(N.leer(pag, P.CUANDO)), via=N.leer(pag, P.VIA) or "",
        id_pub=N.leer(pag, P.ID_PUB) or "", id_subida=N.leer(pag, P.ID_SUBIDA) or "",
        intentos=int(N.leer(pag, P.INTENTOS) or 0), bloqueo=N.leer(pag, P.BLOQUEO) or "",
        error=N.leer(pag, P.ERROR) or "")


def _filtro_estados(prop: str, estados) -> dict:
    return {"or": [{"property": prop, "select": {"equals": e}} for e in estados]}


class _SinBloqueo:
    def latido(self) -> None:
        pass


# ---------------------------------------------------------------- descargas y preparación

class Medios:
    """Descarga cada archivo de Drive una sola vez por ejecución y lo prepara por red."""

    def __init__(self, drive, carpeta: Path, latido=lambda: None):
        self.drive, self.carpeta, self.latido = drive, carpeta, latido
        self._bajados: dict[str, tuple[Path, dict]] = {}
        self._preparados: dict[tuple[str, str], object] = {}

    def video(self, file_id: str, red: str):
        if file_id not in self._bajados:
            self._bajados[file_id] = self.drive.descargar(file_id, self.carpeta, latido=self.latido)
        if (file_id, red) not in self._preparados:
            ruta, meta = self._bajados[file_id]
            prep = preparar(ruta, red, self.carpeta)
            prep.detalle += " · MD5 verificado con Drive" if meta.get("md5_verificado") else ""
            self._preparados[(file_id, red)] = prep
        return self._preparados[(file_id, red)]

    def fotos(self, c: "Contenido") -> list[dict] | None:
        """Fotos de Drive (metadatos, en orden) si el contenido es de fotos; None si es un video.

        Valida sin descargar nada: cantidad, formatos y la proporción que exige Instagram."""
        clave = ("fotos", c.video_id)
        if clave not in self._preparados:
            self._preparados[clave] = self._revisar_fotos(c)
        return self._preparados[clave]

    def _revisar_fotos(self, c: "Contenido") -> list[dict] | None:
        if c.carpeta:
            archivos = self.drive.listar(c.video_id)
        else:
            m = self.drive.metadatos(c.video_id)
            if not m.get("mimeType", "").startswith("image/"):
                return None
            archivos = [m]
        fotos = sorted((a for a in archivos if a.get("mimeType") in FORMATOS_FOTO), key=lambda a: orden_natural(a["name"]))
        otras = [a["name"] for a in archivos if a.get("mimeType", "").startswith("image/") and a not in fotos]
        videos = [a["name"] for a in archivos if a.get("mimeType", "").startswith("video/")]
        if videos:
            raise ErrorValidacion(f"Por ahora los carruseles solo admiten fotos; la carpeta tiene video(s): "
                                  f"{', '.join(videos[:3])}.")
        if otras:
            raise ErrorValidacion(f"Formato de imagen no admitido: {', '.join(otras[:3])}. Usa JPG, PNG, WEBP o HEIC.")
        if not fotos:
            raise ErrorValidacion("La carpeta de Drive no tiene fotos (JPG, PNG, WEBP o HEIC).")
        if len(fotos) > MAX_FOTOS:
            raise ErrorValidacion(f"La carpeta tiene {len(fotos)} fotos; un carrusel admite máximo {MAX_FOTOS}.")
        for red in c.redes:
            if red not in REDES_FOTOS:
                raise ErrorValidacion(f"{red} no admite fotos ni carruseles en este sistema. Quítala de 'Redes'.")
            for a in fotos:
                im = a.get("imageMediaMetadata") or {}
                ancho, alto = im.get("width") or 0, im.get("height") or 0
                if (im.get("rotation") or 0) % 2:
                    ancho, alto = alto, ancho
                motivo = proporcion_fuera(ancho, alto, red)
                if motivo:
                    raise ErrorValidacion(f"La foto '{a['name']}' {motivo}.")
        return fotos

    def album(self, c: "Contenido", red: str) -> Album:
        """Descarga cada foto original (una vez) y la prepara para la red."""
        fotos = []
        for a in self.fotos(c):
            if a["id"] not in self._bajados:
                self._bajados[a["id"]] = self.drive.descargar(a["id"], self.carpeta, tipo="image/", latido=self.latido)
            if (a["id"], red) not in self._preparados:
                ruta, meta = self._bajados[a["id"]]
                foto = preparar_foto(ruta, a["name"], red, self.carpeta)
                foto.detalle += " · MD5 verificado con Drive" if meta.get("md5_verificado") else ""
                self._preparados[(a["id"], red)] = foto
            fotos.append(self._preparados[(a["id"], red)])
        return Album(fotos)

    def portada(self, url: str) -> Path:
        """Descarga la imagen de portada subida a Notion (una URL temporal firmada)."""
        import requests
        clave = hashlib.sha1(url.split("?")[0].encode()).hexdigest()[:12]
        if clave not in self._bajados:
            r = requests.get(url, timeout=60)
            if r.status_code != 200 or not r.headers.get("Content-Type", "").startswith("image/"):
                raise ErrorValidacion("La portada debe ser una imagen (JPG o PNG) subida en la columna Portada.")
            ruta = self.carpeta / f"portada-{clave}{Path(url.split('?')[0]).suffix or '.jpg'}"
            ruta.write_bytes(r.content)
            self._bajados[clave] = (ruta, {})
        return self._bajados[clave][0]


# ---------------------------------------------------------------- motor

class Motor:
    def __init__(self, cfg, notion, adaptador, medios, *, reloj=None, dormir=time.sleep,
                 simulacro: bool = False, bloqueo=None, run: str | None = None):
        self.cfg, self.n, self.adaptador, self.medios = cfg, notion, adaptador, medios
        self.reloj = reloj or (lambda: datetime.now(timezone.utc))
        self.dormir = dormir
        self.simulacro = simulacro
        self.bloqueo = bloqueo or _SinBloqueo()
        self.run = run or uuid.uuid4().hex[:8]
        self.anticipacion = timedelta(minutes=cfg.anticipacion_minutos)
        self.minimo = os.environ.get("PUBLICADOR_LOG") == "minimo"  # registros públicos: sin títulos
        self.tocados: set[str] = set()
        self.reclamadas: set[str] = set()
        self.informe: list[str] = []

    # ------------------------------------------------------------ utilidades

    def _nombre(self, titulo: str | None, pagina_id: str) -> str:
        return f"contenido {pagina_id[:8]}" if self.minimo else f"'{titulo}'"

    def _log(self, texto: str) -> None:
        linea = ("[simulacro] " if self.simulacro else "") + texto
        log.info(linea)
        self.informe.append(linea)

    def _escribir(self, pagina_id: str, props: dict) -> None:
        if not self.simulacro:
            self.n.actualizar(pagina_id, props)

    def _fila(self, f: Fila, **cambios) -> None:
        conv = {"estado": (P.ESTADO, N.opcion), "error": (P.ERROR, N.texto), "bloqueo": (P.BLOQUEO, N.texto),
                "id_subida": (P.ID_SUBIDA, N.texto), "id_pub": (P.ID_PUB, N.texto), "url": (P.URL_PUB, N.enlace),
                "publicado_el": (P.PUBLICADO_EL, N.fecha), "cuando": (P.CUANDO, N.fecha),
                "intentos": (P.INTENTOS, N.numero), "detalle": (P.DETALLE, N.texto)}
        props = {}
        for clave, valor in cambios.items():
            nombre, fn = conv[clave]
            props[nombre] = fn(valor)
            if hasattr(f, clave):
                setattr(f, clave, valor)
        self._escribir(f.id, props)
        if "estado" in cambios:
            self._log(f"{f.red} · {f.cuenta}: {cambios['estado']}" +
                      (f" — {cambios['error']}" if cambios.get("error") else ""))

    def _bloqueo_texto(self, etapa: str) -> str:
        return f"run={self.run}|etapa={etapa}|desde={self.reloj().isoformat()}"

    def _pagina_contenido(self, contenido_id: str | None) -> dict | None:
        if not contenido_id:
            return None
        try:
            pag = self.n.pagina(contenido_id)
        except ErrorPermanente:
            return None
        return None if pag.get("archived") or pag.get("in_trash") else pag

    def _filas_de(self, contenido_id: str) -> list[Fila]:
        pags = self.n.consultar(self.cfg.notion_publicaciones,
                                {"property": P.CONTENIDO, "relation": {"contains": contenido_id}})
        return [leer_fila(p) for p in pags]

    def leer_contenido(self, pag: dict) -> Contenido:
        titulo = N.leer(pag, C.TITULO)
        if not titulo:
            raise ErrorValidacion("Falta el título.")
        cuenta = N.leer(pag, C.CUENTA)
        if not cuenta:
            raise ErrorValidacion("Elige la cuenta (marca) que publica.")
        if cuenta not in self.cfg.cuentas:
            raise ErrorValidacion(f"La cuenta '{cuenta}' no está configurada en el sistema.")
        redes = N.leer(pag, C.REDES) or []
        if not redes:
            raise ErrorValidacion("Elige al menos una red.")
        for red in redes:
            self.cfg.destino(cuenta, red)
        cuando = parsear_fecha(N.leer(pag, C.FECHA), self.cfg.zona_horaria)
        video_id, carpeta = enlace_drive(N.leer(pag, C.URL))
        if carpeta:
            for red in redes:
                if red not in REDES_FOTOS:
                    raise ErrorValidacion(f"{red} no admite fotos ni carruseles en este sistema. Quítala de 'Redes'.")
        portadas = [u for u in (N.leer(pag, C.PORTADA) or []) if u]
        texto = componer_texto(N.leer(pag, C.COPY), N.leer(pag, C.HASHTAGS))
        for red in redes:
            validar_texto(red, texto)
        colab = [c.strip().lstrip("@") for c in re.split(r"[\s,;]+", N.leer(pag, C.COLAB_IG) or "") if c.strip()]
        if len(colab) > 3:
            raise ErrorValidacion("Instagram admite máximo 3 colaboradores.")
        return Contenido(pag["id"], titulo, cuenta, redes, cuando, video_id, portadas[0] if portadas else None, texto,
                         N.leer(pag, C.TITULO_YT) or "", colab, carpeta)

    def _trabajo(self, f: Fila, c: Contenido | None) -> Trabajo:
        return Trabajo(red=f.red, cuenta=f.cuenta, cuando=f.cuando or (c.cuando if c else self.reloj()),
                       titulo=c.titulo if c else "", texto=c.texto if c else "",
                       titulo_youtube=c.titulo_youtube if c else "", colaboradores=c.colaboradores if c else [],
                       portada_url=c.portada_url if c else None,
                       id_subida=f.id_subida or None, id_publicacion=f.id_pub or None, bloqueo_desde=f.bloqueo_desde)

    # ------------------------------------------------------------ ejecución

    def ejecutar(self) -> list[str]:
        self._sincronizar_listos()
        filas = [leer_fila(p) for p in self.n.consultar(self.cfg.notion_publicaciones,
                                                         _filtro_estados(P.ESTADO, P_ACTIVOS))]
        a_la_hora: list[tuple[Fila, Contenido]] = []
        for f in filas:
            try:
                self._procesar(f, a_la_hora)
            except Exception as e:
                self._inesperado(f, e)
        for f, c in sorted(a_la_hora, key=lambda x: x[0].cuando):
            try:
                self._publicar(f, c, nativo=False)
            except Exception as e:
                self._inesperado(f, e)
        self._recalcular()
        if not self.informe:
            self._log("Nada que hacer.")
        return self.informe

    def _inesperado(self, f: Fila, e: Exception) -> None:
        if self.minimo:
            log.error("Error inesperado en %s: %s", f.id[:8], type(e).__name__)
        else:
            log.exception("Error inesperado en %s", f.id)
        if f.id in self.reclamadas:  # ya pudo haber enviado algo: no arriesgar
            self._fila(f, estado=P_REVISION, error=f"Error interno inesperado durante la publicación: {e}")
        else:
            self._log(f"{f.red} · {f.cuenta}: se reintentará en la próxima ejecución ({e})")

    # ------------------------------------------------------------ paso 1: contenidos listos

    def _sincronizar_listos(self) -> None:
        listos = self.n.consultar(self.cfg.notion_contenidos, {"property": C.ESTADO, "select": {"equals": C_LISTO}})
        for pag in listos:
            self.tocados.add(pag["id"])
            try:
                c = self.leer_contenido(pag)
                self.medios.fotos(c)  # fotos: se validan ya (cantidad, formato, proporción) sin descargar
            except ErrorValidacion as e:
                self._escribir(pag["id"], {C.ESTADO: N.opcion(C_ERROR), C.ERROR: N.texto(str(e))})
                self._log(f"{self._nombre(N.leer(pag, C.TITULO), pag['id'])}: Error — {e}")
                continue
            except ErrorTransitorio as e:
                self._log(f"{self._nombre(N.leer(pag, C.TITULO), pag['id'])}: Drive no respondió ({e}); "
                          "lo reviso en la próxima ejecución.")
                continue
            if self.simulacro:
                self._simular_contenido(c)
                continue
            existentes: dict[str, Fila] = {}
            for f in self._filas_de(c.id):
                existentes.setdefault(f.red, f)
            for red in c.redes:
                f = existentes.get(red)
                if f is None:
                    self._crear_fila(c, red)
                elif f.id_pub:
                    continue  # ya se publicó o programó: jamás se repite
                elif f.estado in (P_ERROR, P_CANCELADO):
                    self._fila(f, estado=P_EN_COLA, error="", bloqueo="", id_subida="", intentos=0, cuando=c.cuando)
                elif f.estado == P_EN_COLA:
                    self._fila(f, cuando=c.cuando)
            for red, f in existentes.items():
                if red not in c.redes and f.estado in (P_EN_COLA, P_ERROR):
                    self._fila(f, estado=P_CANCELADO, error="Se quitó esta red del contenido.")
            self._escribir(c.id, {C.ESTADO: N.opcion(C_PROGRAMADO), C.ERROR: N.texto("")})
            self._log(f"{self._nombre(c.titulo, c.id)}: programado en {', '.join(c.redes)} para {c.cuando.isoformat()}")

    def _crear_fila(self, c: Contenido, red: str) -> None:
        via = self.cfg.destino(c.cuenta, red).via
        self.n.crear(self.cfg.notion_publicaciones, {
            P.NOMBRE: N.titulo(f"{red} · {c.titulo}"), P.CONTENIDO: N.relacion([c.id]),
            P.RED: N.opcion(red), P.CUENTA: N.opcion(c.cuenta), P.ESTADO: N.opcion(P_EN_COLA),
            P.CUANDO: N.fecha(c.cuando), P.VIA: N.opcion("Nativo" if via == "nativo" else "Upload-Post"),
            P.INTENTOS: N.numero(0)})

    def _simular_contenido(self, c: Contenido) -> None:
        self._log(f"{self._nombre(c.titulo, c.id)}: datos OK → se programaría en {', '.join(c.redes)} para {c.cuando.isoformat()}")
        for red in c.redes:
            try:
                medio = self.medios.album(c, red) if self.medios.fotos(c) else self.medios.video(c.video_id, red)
                self._log(f"   {red}: {medio.detalle}")
            except ErrorPublicador as e:
                self._log(f"   {red}: PROBLEMA — {e}")

    # ------------------------------------------------------------ paso 2: publicaciones activas

    def _procesar(self, f: Fila, a_la_hora: list) -> None:
        ahora = self.reloj()
        pag = self._pagina_contenido(f.contenido_id)
        if f.contenido_id:
            self.tocados.add(f.contenido_id)
        estado_c = N.leer(pag, C.ESTADO) if pag else C_CANCELADO
        if estado_c == C_CANCELADO:
            if f.estado == P_EN_COLA:
                self._fila(f, estado=P_CANCELADO, error="")
            elif f.estado == P_PROGRAMADO:
                self._fila(f, estado=P_REVISION, error=f"Se canceló en Notion, pero ya estaba programado en "
                                                       f"{f.red}. Bórralo en la plataforma.")
            return
        if estado_c == C_BORRADOR and f.estado == P_EN_COLA:
            return  # el equipo lo pausó devolviéndolo a Borrador
        try:
            c = self.leer_contenido(pag)
        except ErrorValidacion as e:
            if f.estado == P_EN_COLA:
                self._fila(f, estado=P_ERROR, error=f"Datos del contenido: {e}")
                return
            c = None  # igual se puede verificar/recuperar lo que ya se subió
        if f.cuando is None and c:
            self._fila(f, cuando=c.cuando)
        if c and c.cuando != f.cuando:
            if not self._cambio_de_fecha(f, c):
                return
        plan = planificar(red=f.red, via="nativo" if f.via == "Nativo" else "uploadpost", estado=f.estado,
                          cuando=f.cuando, ahora=ahora, anticipacion=self.anticipacion,
                          bloqueo_desde=f.bloqueo_desde, bloqueo_propio=f.bloqueo_run == self.run)
        if plan.accion == "esperar":
            return
        if plan.accion == "vencido":
            self._fila(f, estado=P_ERROR, error=f"No se publicó: {plan.motivo}. Corrige la fecha y vuelve a poner "
                                               "el contenido en 'Listo para publicar'.")
        elif plan.accion == "programar_nativo":
            self._publicar(f, c, nativo=True)
        elif plan.accion == "publicar_a_la_hora":
            a_la_hora.append((f, c))
        elif plan.accion == "verificar":
            self._verificar(f, c)
        elif plan.accion == "recuperar":
            self._recuperar(f, c)

    def _cambio_de_fecha(self, f: Fila, c: Contenido) -> bool:
        if f.estado == P_EN_COLA:
            self._fila(f, cuando=c.cuando)
            return True
        if f.estado == P_PROGRAMADO and f.id_pub and not self.simulacro:
            try:
                self.adaptador(f.cuenta, f.red).reprogramar(self._trabajo(f, c), c.cuando)
                self._fila(f, cuando=c.cuando, error="")
                self._log(f"{f.red} · {f.cuenta}: reprogramado para {c.cuando.isoformat()}")
                return True
            except ErrorPublicador as e:
                self._fila(f, estado=P_REVISION, error=f"Cambiaste la fecha, pero {f.red} no dejó reprogramar ({e}). "
                                                       "Cámbiala directamente en la plataforma.")
                return False
        return True

    def _publicar(self, f: Fila, c: Contenido, *, nativo: bool) -> None:
        if f.id_pub:
            self._fila(f, estado=P_REVISION, error="Ya tiene 'ID publicación': no se vuelve a publicar.")
            return
        if self.simulacro:
            modo = "programaría en la plataforma" if nativo else f"publicaría a las {f.cuando.isoformat()}"
            self._log(f"{f.red} · {f.cuenta}: se {modo}")
            return
        self._fila(f, estado=P_SUBIENDO, bloqueo=self._bloqueo_texto("descarga"), error="")
        if leer_fila(self.n.pagina(f.id)).bloqueo_run != self.run:
            self._log(f"{f.red} · {f.cuenta}: otra ejecución la tomó; la dejo.")
            return
        self.reclamadas.add(f.id)
        adaptador = self.adaptador(f.cuenta, f.red)
        t = self._trabajo(f, c)
        detalle = ""
        try:
            if self.medios.fotos(c):
                res = self._publicar_fotos(f, c, t, adaptador, nativo)
            else:
                video = self.medios.video(c.video_id, f.red)
                detalle = video.detalle
                portada = self.medios.portada(c.portada_url) if c.portada_url else None
                self._fila(f, bloqueo=self._bloqueo_texto("subida"), detalle=detalle)
                guardar = lambda x: self._fila(f, id_subida=x)
                if nativo:
                    res = adaptador.programar(t, video, portada, guardar)
                else:
                    res = adaptador.publicar(t, video, portada, lambda: self._esperar_hasta(f.cuando), guardar)
        except (ErrorValidacion, ErrorPermanente) as e:
            self._fila(f, estado=P_ERROR, error=str(e), bloqueo="")
        except ErrorTransitorio as e:
            self._reintento(f, e)
        except ResultadoIncierto as e:
            self._fila(f, estado=P_REVISION, error=str(e), bloqueo="")
        else:
            self._aplicar(f, res)

    def _publicar_fotos(self, f: Fila, c: Contenido, t: Trabajo, adaptador, nativo: bool) -> Resultado:
        """Igual que un video, con fotos. Los errores los clasifica _publicar (mismo try)."""
        album = self.medios.album(c, f.red)
        if adaptador.fotos_por_url:
            self._alojar(f, album)
        self._fila(f, bloqueo=self._bloqueo_texto("subida"), detalle=album.detalle)
        guardar = lambda x: self._fila(f, id_subida=x)
        if nativo:
            return adaptador.programar_fotos(t, album, guardar)
        return adaptador.publicar_fotos(t, album, lambda: self._esperar_hasta(f.cuando), guardar)

    def _alojar(self, f: Fila, album: Album) -> None:
        """Sube cada foto al cuerpo de la fila de Publicaciones en Notion y guarda su URL temporal (1 h).

        Instagram descarga las fotos desde ahí. Si la foto ya está (un reintento), se reutiliza."""
        existentes = {}
        for b in self.n.hijos(f.id):
            img = b.get("image") or {}
            if b.get("type") == "image" and img.get("type") == "file":
                existentes["".join(x.get("plain_text", "") for x in img.get("caption", []))] = img["file"]["url"]
        for foto in album.fotos:
            leyenda = f"{foto.nombre} · md5 {foto.md5}"
            nombre = Path(foto.nombre).stem + foto.ruta.suffix
            foto.url = existentes.get(leyenda) or self.n.agregar_imagen(f.id, foto.ruta, nombre, leyenda)

    def _esperar_hasta(self, cuando: datetime) -> None:
        while (falta := (cuando - self.reloj()).total_seconds()) > 0:
            self.bloqueo.latido()
            self.dormir(min(falta, 30))

    def _reintento(self, f: Fila, e: Exception) -> None:
        n = f.intentos + 1
        if n >= MAX_INTENTOS:
            self._fila(f, estado=P_ERROR, error=f"Falló {n} veces seguidas: {e}", bloqueo="", intentos=n, id_subida="")
        else:
            self._fila(f, estado=P_EN_COLA, error=f"Reintento {n}/{MAX_INTENTOS - 1}: {e}", bloqueo="",
                       intentos=n, id_subida="")

    def _aplicar(self, f: Fila, res: Resultado) -> None:
        cambios = {"estado": res.estado, "error": res.nota}
        cambios["bloqueo"] = self._bloqueo_texto("procesando") if res.estado == P_SUBIENDO else ""
        if res.id_publicacion:
            cambios["id_pub"] = res.id_publicacion
        if res.url:
            cambios["url"] = res.url
        if res.estado == P_PUBLICADO:
            cambios["publicado_el"] = res.publicado_el or self.reloj()
        self._fila(f, **cambios)

    def _verificar(self, f: Fila, c: Contenido | None) -> None:
        adaptador = self.adaptador(f.cuenta, f.red)
        try:
            res = adaptador.verificar(self._trabajo(f, c))
        except ErrorPermanente as e:
            self._fila(f, estado=P_ERROR, error=str(e))
            return
        except ErrorPublicador as e:
            self._log(f"{f.red} · {f.cuenta}: no pude verificar ({e}); lo intento luego.")
            return
        if res:
            self._aplicar(f, res)
        elif self.reloj() - f.cuando > MAX_RETRASO:
            self._fila(f, estado=P_REVISION, error="La plataforma no confirma la publicación 2 h después de la "
                                                   "hora programada. " + adaptador.pista_sin_confirmar)

    def _recuperar(self, f: Fila, c: Contenido | None) -> None:
        if self.simulacro:
            self._log(f"{f.red} · {f.cuenta}: se recuperaría una subida interrumpida")
            return
        if f.bloqueo_etapa == "descarga" and not f.id_subida:
            self._reintento(f, ErrorTransitorio("se interrumpió durante la descarga"))
            return
        try:
            r = self.adaptador(f.cuenta, f.red).recuperar(self._trabajo(f, c))
        except ErrorTransitorio as e:
            self._log(f"{f.red} · {f.cuenta}: no pude comprobar la subida interrumpida ({e}); lo intento luego.")
            return
        except ErrorPublicador as e:
            self._fila(f, estado=P_REVISION, error=str(e), bloqueo="")
            return
        if r == REINTENTAR:
            self._reintento(f, ErrorTransitorio("se interrumpió y la plataforma confirma que no se publicó"))
        elif r == ESPERAR:
            if f.cuando and self.reloj() - f.cuando > MAX_RETRASO:
                self._fila(f, estado=P_REVISION, error="La plataforma lleva más de 2 h procesando. Revisa el perfil.",
                           bloqueo="")
            else:
                self._fila(f, bloqueo=self._bloqueo_texto("procesando"))
        else:
            self._aplicar(f, r)

    # ------------------------------------------------------------ paso 3: estado de cada contenido

    def _recalcular(self) -> None:
        if self.simulacro:
            return
        ids = set(self.tocados)
        ids |= {p["id"] for p in self.n.consultar(self.cfg.notion_contenidos,
                                                  {"property": C.ESTADO, "select": {"equals": C_ATENCION}})}
        for cid in ids:
            pag = self._pagina_contenido(cid)
            if not pag or N.leer(pag, C.ESTADO) in (C_BORRADOR, C_LISTO, C_CANCELADO, C_ERROR):
                continue
            filas = self._filas_de(cid)
            if not filas:
                continue
            nuevo = estado_contenido([f.estado for f in filas])
            avisos = []
            for f in filas:
                if f.estado == P_BORRADORES:
                    avisos.append(f"{f.red}: está en borradores. Publícalo desde la app y cambia su fila a 'Publicado'.")
                elif f.estado in P_ATENCION and f.error:
                    avisos.append(f"{f.red}: {f.error}")
            props = {C.ESTADO: nuevo, C.RESULTADO: resumen_resultado([(f.red, f.estado) for f in filas]),
                     C.ERROR: "\n".join(avisos)}
            actual = {C.ESTADO: N.leer(pag, C.ESTADO), C.RESULTADO: N.leer(pag, C.RESULTADO) or "",
                      C.ERROR: N.leer(pag, C.ERROR) or ""}
            if props != actual:
                self.n.actualizar(cid, {C.ESTADO: N.opcion(nuevo), C.RESULTADO: N.texto(props[C.RESULTADO]),
                                        C.ERROR: N.texto(props[C.ERROR])})
                if nuevo != actual[C.ESTADO]:
                    self._log(f"{self._nombre(N.leer(pag, C.TITULO), cid)}: {nuevo} ({props[C.RESULTADO]})")
