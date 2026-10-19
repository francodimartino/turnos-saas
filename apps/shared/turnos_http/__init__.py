"""Errores HTTP compartidos por los backends FastAPI (dashboard-backend y admin-backend).

Forma única de toda respuesta de error de negocio: `{"detail": {"codigo": ..., "mensaje": ...}}`. El `codigo` es
estable (lo usan los frontends); el `mensaje` es texto para la persona usuaria, sin datos internos.

Este paquete no importa código de las apps: define las piezas y cada backend las usa. Cualquier traductor nuevo
de errores a HTTP que sea común a los backends se agrega acá.
"""

from .errores import (
    cuerpo_error,
    error_http,
    http_desde_error_auth,
    no_encontrado,
    respuesta_error,
)

__all__ = ["cuerpo_error", "error_http", "http_desde_error_auth", "no_encontrado", "respuesta_error"]
