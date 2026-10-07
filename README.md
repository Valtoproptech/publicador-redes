# Publicador automático Notion → redes

Publica en Instagram, Facebook, TikTok y YouTube los videos (y en Instagram y Facebook también fotos, carruseles y
Stories) marcados como **Listo para publicar** en Notion, con el archivo **original** de Google Drive y a la hora indicada.

- Uso diario (equipo): instrucciones dentro de la página de Notion **📤 Publicador automático de redes**.
- Configuración inicial: [docs/02-puesta-en-marcha.md](docs/02-puesta-en-marcha.md)
- Por qué está hecho así: [docs/01-decisiones.md](docs/01-decisiones.md)

```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m publicador --help
.venv/bin/python -m unittest discover -s tests -t .
```
