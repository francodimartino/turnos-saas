"""Respuesta uniforme cuando la base no atiende a tiempo (pool agotado o query colgada)."""

import asyncio

CODIGO_SERVICIO_OCUPADO = "servicio_ocupado"
MENSAJE_SERVICIO_OCUPADO = "El servicio está ocupado. Reintentá en unos segundos."

# `acquire_timeout` del pool y `command_timeout` de asyncpg lanzan `asyncio.TimeoutError`
# (en Python < 3.11 no es el `TimeoutError` builtin, por eso van los dos).
ERRORES_SERVICIO_OCUPADO: tuple[type[BaseException], ...] = (asyncio.TimeoutError, TimeoutError)


def cuerpo_servicio_ocupado() -> dict:
    """Cuerpo JSON del 503: mismo formato `detail.codigo/mensaje` que el resto de los errores."""
    return {"detail": {"codigo": CODIGO_SERVICIO_OCUPADO, "mensaje": MENSAJE_SERVICIO_OCUPADO}}
