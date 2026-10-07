"""Nombres de las propiedades de Notion. Si renombras una columna en Notion, cámbiala aquí."""

# Base "Contenidos": una fila por video. La llena el equipo.
class C:
    TITULO = "Título"
    ESTADO = "Estado"
    CUENTA = "Cuenta"
    REDES = "Redes"
    FECHA = "Fecha de publicación"
    URL = "URL"                   # archivo de Drive (video o foto) o carpeta de Drive (carrusel de fotos)
    PORTADA = "Portada"
    COPY = "Copy"
    HASHTAGS = "Hashtags"
    TITULO_YT = "Título YouTube"  # opcional (YouTube no se usa); `verificar` no la exige
    FORMATO = "Formato"           # opcional: ayuda visual (Video/Foto/Carrusel/Texto); el motor solo lee "Story"
    COLAB_IG = "Colaboradores IG"
    RESULTADO = "Resultado"
    ERROR = "Error"
    PUBLICACIONES = "Publicaciones"


# Base "Publicaciones": una fila por red. La crea y mantiene el sistema.
class P:
    NOMBRE = "Nombre"
    CONTENIDO = "Contenido"
    RED = "Red"
    CUENTA = "Cuenta"
    ESTADO = "Estado"
    CUANDO = "Programado para"
    PUBLICADO_EL = "Publicado el"
    ID_PUB = "ID publicación"
    ID_SUBIDA = "ID de subida"
    URL_PUB = "URL publicación"
    ERROR = "Error"
    INTENTOS = "Intentos"
    BLOQUEO = "Bloqueo"
    DETALLE = "Detalle técnico"
    VIA = "Vía"
