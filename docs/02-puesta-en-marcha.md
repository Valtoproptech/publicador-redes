# Puesta en marcha (una sola vez, costo 0)

Tiempo aproximado: 1–2 horas. Ninguno de estos servicios pide tarjeta. Cada token se guarda primero en una
carpeta privada de tu Mac (`.secretos/`) y después se sube **cifrado** a GitHub. Nunca queda en el código.

## 1. Notion (5 min)
1. https://www.notion.so/profile/integrations → **Nueva integración** → nombre "Publicador", tipo *Interna* → Guardar.
2. Copia el **Secreto de integración interna** y guárdalo con `.venv/bin/python -m publicador guardar-secreto notion`
3. En Notion, abre **📤 Publicador automático de redes** → `•••` → **Conexiones** → agrega "Publicador".

## 2. Google: Drive + YouTube (15 min, sin tarjeta)
1. https://console.cloud.google.com → crea un proyecto (por ejemplo `publicador-redes`).
2. **APIs y servicios → Biblioteca** → habilita **Google Drive API** y **YouTube Data API v3**.
3. **Pantalla de consentimiento OAuth** → Externo → completa nombre y email → en *Público*, pulsa **Publicar app** (así el permiso no vence a los 7 días).
4. **Credenciales → Crear → ID de cliente OAuth → App de escritorio** → Descargar JSON → guárdalo como `credenciales/google-oauth-cliente.json`.
5. Conecta Drive con la cuenta que **ve los videos**: `.venv/bin/python -m publicador conectar-google google-drive --para drive`
   (Google avisará "app no verificada": es tu propia app → *Configuración avanzada → Ir a…*.)
6. Por cada canal de YouTube: `.venv/bin/python -m publicador conectar-google youtube-bambu --para youtube`
7. Pide la auditoría de YouTube (gratis; sin ella los videos quedan privados): https://support.google.com/youtube/contact/yt_api_form

## 3. Meta: Instagram + Facebook (20 min)
Requisito: cada Instagram es **cuenta profesional** y está **vinculada a su página de Facebook**.
1. https://developers.facebook.com → **Crear app** → tipo *Empresa* → vincúlala a tu portafolio comercial.
2. https://business.facebook.com/settings → **Usuarios del sistema** → Agregar (Administrador) → **Asignar activos**: cada página y cada Instagram, con control total.
3. En ese usuario → **Generar token** → la app → vencimiento **Nunca** → permisos:
   `instagram_basic, instagram_content_publish, pages_show_list, pages_read_engagement, pages_manage_posts, business_management`
4. Guárdalo con `.venv/bin/python -m publicador guardar-secreto meta` y luego corre `.venv/bin/python -m publicador descubrir-meta`. Pega el resultado en `config.toml`.

## 4. TikTok (15 min)
1. https://developers.tiktok.com → inicia sesión → **Manage apps → Connect an app**.
2. Agrega los productos **Login Kit** (plataforma *Desktop*, redirect URI `http://localhost:8765/callback/`) y **Content Posting API**.
3. Scopes: `user.info.basic` y `video.upload`.
4. En **Sandbox**, agrega como *Target users* las cuentas de TikTok de cada marca.
5. Por cada cuenta: `.venv/bin/python -m publicador conectar-tiktok tiktok-bambu` (la primera vez pide el *Client key* y el *Client secret* de la app).

## 5. Probar en tu Mac
```
.venv/bin/python -m publicador verificar
.venv/bin/python -m publicador ejecutar --simulacro
```
El primero debe mostrar todo con ✅. El segundo descarga los videos en "Listo para publicar", revisa su calidad y dice qué haría, **sin publicar ni escribir nada**.

## 6. Dejarlo automático en GitHub
```
.venv/bin/python -m publicador subir-a-github
```
Cifra los tokens, guarda la clave y `config.toml` como secrets de GitHub y sube el archivo cifrado. Desde ahí corre
solo cada 5 minutos, con el Mac apagado. Para verlo: pestaña **Actions** del repositorio.

## Mantenimiento
- **Agregar una marca o red:** su sección en `config.toml` + conectar la cuenta (paso 2, 3 o 4) + `subir-a-github`.
- **Qué pasó con un video:** columna *Error* en Notion. Historial: pestaña **Actions** en GitHub.
- **Nada vence**, salvo que alguien revoque un permiso (el error lo dirá). Si TikTok no se usa por un año, hay que reconectarlo.
