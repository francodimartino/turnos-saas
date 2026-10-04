"""platform_calendar — OAuth2 y sync unidireccional con Google Calendar (ARCHITECTURE.md §4.3, §7.1).

Las apps importan solo desde acá (`ServicioCalendario`, `crear_calendario`, los tipos): la implementación sobre
Google vive en `google` y no se importa desde fuera de este paquete. Las credenciales de cada profesional se
guardan vía `platform_secrets`; las del proyecto Google salen de variables de entorno (`ConfigGoogle.desde_entorno`).
"""

import time
from collections.abc import Callable

import httpx
from platform_secrets.interfaz import AlmacenSecretos

from platform_calendar.config import ConfigGoogle
from platform_calendar.errores import (
    ErrorApiCalendar,
    ErrorCalendar,
    ErrorConexionEliminada,
    ErrorConfiguracionCalendar,
    ErrorEstadoOAuth,
    ErrorOAuth,
    ErrorRedCalendar,
    ErrorTokenVencido,
)
from platform_calendar.google import CalendarioGoogle
from platform_calendar.interfaz import (
    AccionSync,
    BookingCalendario,
    EstadoOAuth,
    EventoNoLimpiado,
    ResultadoSync,
    ServicioCalendario,
)


def crear_calendario(
    config: ConfigGoogle,
    almacen: AlmacenSecretos,
    http: httpx.AsyncClient,
    reloj: Callable[[], float] = time.time,
) -> ServicioCalendario:
    """Servicio de calendario con el cliente HTTP inyectado (en tests, con transporte simulado)."""
    return CalendarioGoogle(config, almacen, http, reloj)


__all__ = [
    "AccionSync",
    "BookingCalendario",
    "ConfigGoogle",
    "ErrorApiCalendar",
    "ErrorCalendar",
    "ErrorConexionEliminada",
    "ErrorConfiguracionCalendar",
    "ErrorEstadoOAuth",
    "ErrorOAuth",
    "ErrorRedCalendar",
    "ErrorTokenVencido",
    "EstadoOAuth",
    "EventoNoLimpiado",
    "ResultadoSync",
    "ServicioCalendario",
    "crear_calendario",
]
