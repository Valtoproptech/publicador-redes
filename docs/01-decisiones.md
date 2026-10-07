# Decisiones de arquitectura

Registro para mantener y ampliar el sistema. Las decisiones nuevas se agregan al final; las viejas no se borran.
Última revisión: 2026-10-07.

## Resumen en una línea
Notion (qué y cuándo) → GitHub Actions cada 5 min → descarga el **original** de Drive → lo sube por la **API oficial** de cada red → escribe el resultado en Notion. **Costo: 0.**

```
Notion 🎬 Contenidos ──(Listo para publicar)──► GitHub Actions (cada 5 min, gratis)
                                                   │ Drive API: original byte a byte + MD5
                                                   │ ffprobe: ¿cumple specs? → intacto / remux / recodificar solo esa red
                                                   ├─► Instagram  Graph API (resumable)       · publica a la hora exacta (también Stories)
                                                   ├─► Facebook   Reels API                   · programación nativa (Stories: a la hora exacta)
                                                   ├─► YouTube    Data API (resumable)        · programación nativa
                                                   └─► TikTok     app propia (archivo original)  · a borradores, a la hora
Notion ⚙️ Publicaciones ◄── estado, ID, URL, error, detalle técnico (una fila por red)
```

## Decisiones

| # | Decisión | Por qué | Alternativas descartadas |
|---|---|---|---|
| D0 | **Costo cero** es requisito | Si hubiera que pagar, conviene más un planificador comercial | — |
| D1 | **APIs oficiales directas** para Instagram, Facebook y YouTube | Máxima calidad (se sube el archivo original), cero intermediarios, gratis | GHL, Metricool o Make: un paso más que puede recomprimir |
| D2 | **TikTok con app propia en modo borradores** | Gratis y con el archivo original. TikTok no deja que apps de uso interno publiquen solas en público (lo dice su guía de auditoría), así que una persona da el último toque desde la notificación. Máximo 5 borradores pendientes por día y cuenta | Upload-Post o Zernio para publicar automático: TikTok es de pago en Upload-Post y Zernio gratis solo cubre 2 cuentas. Automatizar el navegador: frágil y contra los términos de TikTok |
| D3 | La vía de cada red es **configurable** (`via = "nativo" \| "uploadpost"`) | YouTube puede ir por el plan gratis de Upload-Post (10 videos/mes) mientras Google aprueba la auditoría | — |
| D4 | **Dos bases en Notion**: Contenidos (una fila por video, la llena el equipo) y Publicaciones (una fila por red, la mantiene el sistema) | El equipo escribe una vez; cada red tiene su propio estado, ID, error y reintento. Los meses son vistas, no bases | Una fila por red a mano; columnas por red |
| D5 | **Drive con OAuth de la cuenta del dueño** (solo lectura) | Ve todo lo que esa cuenta ve, sin compartir carpetas. Se verifica el MD5 contra Drive | Enlace público de Drive (falla con archivos de más de 100 MB) |
| D6 | **Meta con token de "usuario del sistema"** | No vence nunca: cero mantenimiento | Tokens de usuario (vencen a los 60 días) |
| D7 | **GitHub Actions en un repositorio público** | Minutos gratis e ilimitados en repos públicos; Python y ffmpeg incluidos. El cron puede atrasarse, así que cada publicación se toma hasta 45 min antes y el proceso **espera la hora exacta**. `concurrency` garantiza una sola ejecución a la vez | Google Cloud (pide tarjeta y podía costar céntimos); repo privado (2000 min/mes no alcanzan); tu Mac (tendría que estar encendido); Apps Script (sin ffmpeg y con límites de 50 MB y 6 min) |
| D8 | **Qué es público y qué no** en el repo | Público: solo el código. Privado: `config.toml` (secret de GitHub) y los tokens (archivo **cifrado** en el repo; la clave solo está en los secrets de GitHub y en tu Mac). Los registros usan modo mínimo (sin títulos ni textos) y GitHub enmascara cada token. Los tokens de Meta viajan en cabeceras, nunca en URLs | — |
| D9 | **Calidad: intacto → remux → recodificar**, en ese orden, por red | Solo se toca el video si la red lo rechazaría | Recodificar siempre |
| D10 | **Anti-duplicados en 5 capas** | 1) Una ejecución a la vez. 2) La fila pasa a *Subiendo* y se relee antes de tocar la red. 3) Con *ID publicación* jamás se republica. 4) IDs intermedios guardados al instante + conciliación tras cortes. 5) Si hay duda → *Requiere revisión*, nunca un reintento ciego | — |
| D11 | **Portada: la imagen se sube directo en Notion** | Notion entrega una URL temporal que Instagram acepta; no hace falta ningún almacenamiento extra | Guardarla en Cloud Storage (de pago) |
| D12 | **Mantener vivo el cron** | GitHub apaga los cron de repos públicos tras 60 días sin actividad; el workflow hace un commit vacío cada 45 días. Los tokens de TikTok que se renuevan se guardan cifrados con un commit | — |
| D13 | **Columna "Formato" en Contenidos** (Video / Foto / Carrusel / Texto / Story) | Ayuda visual para ordenar el planner. Se elige **a mano**; el motor **no la lee** salvo el valor **Story** (ver D15): en lo demás decide el enlace (ver D14). Es opcional: `verificar` no la exige | Detectarla automáticamente; usarla para decidir cómo se publica |
| D14 | **Fotos y carruseles: la URL apunta a una carpeta de Drive** (o a una sola imagen). 1 foto = post de foto, 2–10 = carrusel, en orden por nombre (`1, 2, … 10`). Solo Instagram y Facebook | Instagram **solo** acepta fotos por URL pública: el sistema sube cada foto al cuerpo de la fila de *Publicaciones* en Notion (API de archivos, gratis, máx. 5 MiB por archivo en el plan gratis) y le pasa a Instagram esa URL temporal (1 h). Facebook recibe el archivo directo y programa de forma nativa. Original intacto salvo que la red lo exija (PNG/WEBP/HEIC → JPEG en Instagram). La proporción nunca se recorta: si no cabe, error claro. Se valida al marcar *Listo* con los metadatos de Drive, sin descargar | Repo público o GitHub Pages (las fotos quedarían públicas antes de tiempo); hacer pública la carpeta de Drive (cambia permisos y Drive no da un enlace directo fiable); fotos no publicadas de Facebook como URL para Instagram (recomprime). TikTok exige un dominio verificado propio para fotos y YouTube no tiene API de fotos |

| D15 | **Stories: Formato = Story.** La URL es un archivo (1 Story) o una carpeta de Drive (varias Stories seguidas, en orden por nombre, fotos y videos mezclados, máx. 10). Solo Instagram y Facebook | Las dos tienen API oficial y gratis para Stories (Instagram: contenedor `STORIES`; Facebook: `photo_stories` / `video_stories`), pero **ninguna permite programarlas**: todo se sube hasta 45 min antes y se publica **a la hora exacta**, en orden. Fotos de Instagram por URL de Notion (igual que D14); videos resumables con el original. Duración de videos: Instagram 3–60 s, Facebook 3–90 s, se valida al marcar *Listo* con los metadatos de Drive. Si se corta a mitad de una secuencia, lo que ya salió nunca se repite: pasa a *Requiere revisión* | Una casilla "Es Story" aparte; una fila por Story (más trabajo para el equipo) |

## Tipos de error (qué hace el sistema con cada uno)
| Tipo | Ejemplo | Resultado |
|---|---|---|
| Validación | Falta la hora; el enlace de Drive es una carpeta | Contenido en **Error** con el motivo. El equipo corrige y reactiva |
| Permanente | La red rechazó el video | Esa red en **Error** |
| Transitorio | Caída de red, 5xx, límite de peticiones (antes del paso final) | Vuelve a **En cola**. Hasta 3 intentos |
| Incierto | Sin respuesta en la llamada que publica | **Requiere revisión**. Nunca se reintenta solo |

## Límites conocidos (v1)
- Fotos y carruseles: solo Instagram y Facebook, solo fotos (sin videos dentro del carrusel), máximo 10. En Instagram cada foto debe estar entre 4:5 (vertical) y 1.91:1 (horizontal) y todas se recortan al formato de la primera.
- Stories: sin stickers (enlace, encuesta, ubicación, música, mención con sticker), porque la API no los permite. El copy y los hashtags **no** se publican (no hay texto en Stories): todo debe ir dentro de la imagen o el video. Lo ideal es 9:16 (1080x1920). En Facebook una foto o video ya usado en otro post no puede ser Story. Instagram cuenta cada Story dentro de su límite de publicaciones por API (50 por día).
- El copy es el mismo para todas las redes (el título de YouTube puede ser distinto).
- Un cambio de copy **después** de que Facebook o YouTube ya lo tienen programado no se sincroniza. La fecha sí se sincroniza.
- YouTube deja los videos en privado hasta que Google aprueba la auditoría del proyecto. Mientras tanto: `via = "uploadpost"` (gratis, 10 videos/mes).
- TikTok: el último paso es manual (publicar el borrador) y hay un máximo de 5 borradores pendientes por día y cuenta. La app de TikTok funciona en modo *sandbox* (hasta 10 cuentas propias); si TikTok cambia esa política habrá que pedir la revisión de la app.
- Las respuestas de Upload-Post (solo si se usa como respaldo) no están documentadas del todo; el código las lee de forma defensiva.

## Mapa del código
| Archivo | Qué hace |
|---|---|
| `publicador/reglas.py` | Decisiones puras (planificación, specs, estados). Sin red |
| `publicador/motor.py` | Orquestación y anti-duplicados |
| `publicador/redes/*.py` | Un adaptador por red. Para agregar una red: nuevo adaptador + una línea en `redes/__init__.py` |
| `publicador/media.py` / `drive.py` | Calidad de video / descarga verificada |
| `publicador/imagenes.py` | Calidad de fotos (intacta o JPEG de alta calidad, solo si la red lo exige). `Secuencia` = Stories en orden |
| `publicador/secretos.py` | Tokens: carpeta local o archivo cifrado para GitHub |
| `.github/workflows/publicar.yml` | La ejecución automática cada 5 min |
| `publicador/esquema.py` | Nombres de columnas de Notion (si se renombra una columna, se cambia aquí) |
| `tests/` | 94 pruebas: `.venv/bin/python -m unittest discover -s tests -t .` |
