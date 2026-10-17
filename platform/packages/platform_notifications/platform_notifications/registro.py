"""Registro de intentos de envío en `notifications_log` (§2.10, §6.4).

`RegistradorIntentos` es la costura inyectable: el envío no sabe con qué rol de base escribe. Dos
implementaciones sobre una transacción propia con contexto de tenant (los reintentos corren después de que la
transacción de la request ya cerró, así que el registrador abre la suya con `abrir`, p. ej.
`lambda: tenant_tx(pool, tenant_id)`).

La única implementación sobre la base es `RegistradorFuncion`: `public.record_notification_attempt(...)`
(SECURITY DEFINER), que usan el bot, el dashboard y el job de vencimiento. Ningún rol de aplicación escribe
`notifications_log` con SQL directo.

`last_error` es solo código/mensaje del proveedor: la tabla y la función lo truncan a 500 caracteres.
"""

import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

AbrirTransaccion = Callable[[], AbstractAsyncContextManager[Any]]


class RegistradorIntentos(ABC):
    @abstractmethod
    async def registrar(
        self,
        *,
        end_customer_id: uuid.UUID,
        channel: str,
        template: str | None,
        status: str,
        retry_count: int = 0,
        last_error: str | None = None,
        notification_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        """Crea la fila (sin `notification_id`) o actualiza el intento en curso; devuelve el id de la fila."""


class RegistradorFuncion(RegistradorIntentos):
    _SQL = "select public.record_notification_attempt($1, $2, $3, $4, $5, $6, $7)"

    def __init__(self, abrir: AbrirTransaccion) -> None:
        self._abrir = abrir

    async def registrar(
        self, *, end_customer_id, channel, template, status, retry_count=0, last_error=None, notification_id=None
    ):
        async with self._abrir() as conn:
            return await conn.fetchval(
                self._SQL, end_customer_id, channel, template, status, retry_count, last_error, notification_id
            )
