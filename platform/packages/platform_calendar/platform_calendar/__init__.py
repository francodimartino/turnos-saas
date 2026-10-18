"""platform_calendar — OAuth2 y sync unidireccional con Google Calendar (ARCHITECTURE.md §4.3, §7.1).

Las apps importan solo desde acá (`ServicioCalendario`, `crear_calendario`, los tipos): la implementación sobre
Google vive en `google` y no se importa desde fuera de este paquete. Las credenciales de cada profesional se
guardan vía `platform_secrets`; las del proyecto Google salen de variables de entorno (`ConfigGoogle.desde_entorno`).

Flujo OAuth: `url_autorizacion(tenant, profesional, usuario)` firma un `state` (HMAC, 10 minutos) atado al usuario de
la sesión que inicia la conexión y con un nonce aleatorio. `redirect_uri` apunta a una página del dashboard que hace
el `POST /calendar/callback` (no a un GET del backend, así la cookie de sesión puede ser `SameSite=Strict`).
`verificar_state` devuelve `EstadoOAuth` (tenant, profesional, usuario, nonce y vencimiento); el llamador compara
tenant y usuario con los de la sesión y consume el nonce una sola vez en la base (`consume_oauth_state_nonce`) antes
de llamar a `completar_autorizacion`, que además vuelve a exigir tenant y usuario.
"""

import time
import uuid
from collections.abc import Callable

import httpx
from platform_secrets import Secreto
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
    cache_tokens: dict[uuid.UUID, tuple[Secreto, float]] | None = None,
) -> ServicioCalendario:
    """Servicio de calendario con el cliente HTTP inyectado (en tests, con transporte simulado).

    `cache_tokens`: dict compartido (integración -> (token de acceso, vencimiento)) para que varios servicios, uno por
    operación, reutilicen los tokens de acceso; sin él cada servicio tiene su propia caché.
    """
    return CalendarioGoogle(config, almacen, http, reloj, cache_tokens)


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
