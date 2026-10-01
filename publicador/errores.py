"""Tipos de error. El tipo decide qué hace el sistema, así que elegir bien importa."""


class ErrorPublicador(Exception):
    """Base."""


class ErrorValidacion(ErrorPublicador):
    """Falta o está mal un dato en Notion. Nada se envió. El equipo corrige y reintenta."""


class ErrorPermanente(ErrorPublicador):
    """La plataforma rechazó la petición de forma definitiva. No se publicó nada."""


class ErrorTransitorio(ErrorPublicador):
    """Fallo temporal (red, 5xx, límite de peticiones) ANTES del paso final. Se reintenta solo."""


class ResultadoIncierto(ErrorPublicador):
    """Falló el paso final y no sabemos si se publicó. Nunca se reintenta solo: lo revisa una persona."""
