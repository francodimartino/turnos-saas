"""Registro de intentos de envío en `notifications_log` (§2.10, §6.4).

`RegistradorIntentos` es la costura inyectable: el envío no sabe con qué rol de base escribe. Dos
implementaciones sobre una transacción propia con contexto de tenant (los reintentos corren después de que la
transacción de la request ya cerró, así que el registrador abre la suya con `abrir`, p. ej.
`lambda: tenant_tx(pool, tenant_id)`):

- `RegistradorDirecto`: SQL directo sobre la tabla, para `bot_service` y `dashboard_service` (tienen grants).
- `RegistradorFuncion`: `public.record_notification_attempt(...)` (SECURITY DEFINER), para `scheduler_job`,
  que no tiene grants sobre la tabla.

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


class RegistradorDirecto(RegistradorIntentos):
    # tenant_id lo fuerza el trigger desde end_customers; la columna es not null, así que se manda el del contexto.
    _INSERTAR = (
        "insert into public.notifications_log "
        "(tenant_id, end_customer_id, channel, template, status, retry_count, last_error, sent_at) "
        "values (nullif(current_setting('app.current_tenant_id', true), '')::uuid, $1, $2, $3, $4, $5, "
        "left($6, 500), case when $4 = 'sent' then now() end) returning id"
    )
    # Solo un intento todavía 'pending' se actualiza: 'sent' y 'failed' son finales.
    _ACTUALIZAR = (
        "update public.notifications_log set status = $2, retry_count = $3, last_error = left($4, 500), "
        "sent_at = case when $2 = 'sent' then now() end "
        "where id = $1 and end_customer_id = $5 and status = 'pending' returning id"
    )

    def __init__(self, abrir: AbrirTransaccion) -> None:
        self._abrir = abrir

    async def registrar(
        self, *, end_customer_id, channel, template, status, retry_count=0, last_error=None, notification_id=None
    ):
        async with self._abrir() as conn:
            if notification_id is None:
                return await conn.fetchval(
                    self._INSERTAR, end_customer_id, channel, template, status, retry_count, last_error
                )
            actualizado = await conn.fetchval(
                self._ACTUALIZAR, notification_id, status, retry_count, last_error, end_customer_id
            )
            if actualizado is None:
                raise LookupError("notificación inexistente, de otro tenant o ya en estado final")
            return actualizado
