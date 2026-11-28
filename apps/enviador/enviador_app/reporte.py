"""Reporte de fallos a Sentry (proyecto `turnos-enviador`): solo etiquetas con ids internos, nunca datos del cliente."""

from typing import Any

import sentry_sdk


def reportar_a_sentry(descripcion: str, **etiquetas: Any) -> None:
    """Reporta un fallo con etiquetas (`tenant_id`, `motivo`...). Nunca recibe el texto, el destino ni el token."""
    with sentry_sdk.new_scope() as scope:
        for clave, valor in etiquetas.items():
            scope.set_tag(clave, str(valor))
        sentry_sdk.capture_message(descripcion, level="error")
