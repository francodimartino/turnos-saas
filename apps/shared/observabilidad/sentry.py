"""Sentry sin datos personales (ARCHITECTURE.md §6.1): los datos viven en EE.UU."""

import copy
from typing import Any

import sentry_sdk

from .pii import limpiar_valor


def sentry_before_send(evento: dict, hint: Any = None) -> dict:
    """Copia del evento sin teléfono, nombre, channel_user_id ni cuerpos de mensajes (cualquier nivel)
    y con los teléfonos dentro de textos reemplazados por '[filtrado]'."""
    return limpiar_valor(copy.deepcopy(evento))


def iniciar_sentry(dsn: str | None, servicio: str) -> bool:
    """Inicializa Sentry. Con DSN vacío no hace nada (dev/test). Devuelve si inicializó."""
    if not dsn:
        return False
    sentry_sdk.init(
        dsn=dsn,
        send_default_pii=False,
        before_send=sentry_before_send,
        server_name=servicio,
    )
    return True
