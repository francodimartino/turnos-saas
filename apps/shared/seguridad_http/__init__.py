"""Seguridad HTTP compartida por los tres backends: headers de seguridad y tope de tamaño del body.

Orden recomendado en cada app (el último `add_middleware` es el más externo):
logs > `MiddlewareHeadersSeguridad` > CORS > `MiddlewareLimiteBody` > origen/cookie > rutas.
Así la 413 también lleva los headers de seguridad y los de CORS.
"""

from .middleware import (
    LIMITE_BODY_POR_DEFECTO,
    VARIABLE_LIMITE_BODY,
    MiddlewareHeadersSeguridad,
    MiddlewareLimiteBody,
    limite_body_desde_entorno,
)

__all__ = [
    "LIMITE_BODY_POR_DEFECTO",
    "VARIABLE_LIMITE_BODY",
    "MiddlewareHeadersSeguridad",
    "MiddlewareLimiteBody",
    "limite_body_desde_entorno",
]
