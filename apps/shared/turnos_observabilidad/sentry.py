"""Sentry sin datos personales (ARCHITECTURE.md §6.1): los datos viven en EE.UU."""

import copy
from typing import Any

import sentry_sdk

from .pii import limpiar_valor


def sentry_before_send(evento: Any, hint: Any = None) -> Any:
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
        include_local_variables=False,
        before_send=sentry_before_send,
        server_name=servicio,
    )
    return True


def reportar_a_sentry(descripcion: str, **etiquetas: Any) -> None:
    """Reporta un fallo a Sentry con etiquetas (ids internos como `tenant_id`, un motivo corto), nunca datos del
    cliente: ni el texto del aviso, ni el destinatario, ni el token."""
    with sentry_sdk.new_scope() as scope:
        for clave, valor in etiquetas.items():
            scope.set_tag(clave, str(valor))
        sentry_sdk.capture_message(descripcion, level="error")
