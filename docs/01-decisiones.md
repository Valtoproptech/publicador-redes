# Decisiones de arquitectura

Registro para mantener y ampliar el sistema. Las decisiones nuevas se agregan al final; las viejas no se borran.
Última revisión: 2026-09-29.

## Resumen en una línea
Notion (qué y cuándo) → GitHub Actions cada 5 min → descarga el **original** de Drive → lo sube por la **API oficial** de cada red → escribe el resultado en Notion. **Costo: 0.**

```
Notion 🎬 Contenidos ──(Listo para publicar)──► GitHub Actions (cada 5 min, gratis)
                                                   │ Drive API: original byte a byte + MD5
                                                   │ ffprobe: ¿cumple specs? → intacto / remux / recodificar solo esa red
                                                   ├─► Instagram  Graph API (resumable)       · publica a la hora exacta
                                                   ├─► Facebook   Reels API                   · programación nativa
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

## Tipos de error (qué hace el sistema con cada uno)
| Tipo | Ejemplo | Resultado |
|---|---|---|
| Validación | Falta la hora; el enlace de Drive es una carpeta | Contenido en **Error** con el motivo. El equipo corrige y reactiva |
| Permanente | La red rechazó el video | Esa red en **Error** |
| Transitorio | Caída de red, 5xx, límite de peticiones (antes del paso final) | Vuelve a **En cola**. Hasta 3 intentos |
| Incierto | Sin respuesta en la llamada que publica | **Requiere revisión**. Nunca se reintenta solo |

## Límites conocidos (v1)
- Solo video vertical u horizontal (Reels, Shorts, TikTok). Carruseles y fotos: pendiente.
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
| `publicador/secretos.py` | Tokens: carpeta local o archivo cifrado para GitHub |
| `.github/workflows/publicar.yml` | La ejecución automática cada 5 min |
| `publicador/esquema.py` | Nombres de columnas de Notion (si se renombra una columna, se cambia aquí) |
| `tests/` | 48 pruebas: `.venv/bin/python -m unittest discover -s tests -t .` |
