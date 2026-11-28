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


class SinRegistro(RegistradorIntentos):
    """Para un aviso sin ficha de cliente (p. ej. al negocio) o sin su id: `record_notification_attempt` exige el
    cliente, así que el envío sale igual y no deja fila en `notifications_log`."""

    async def registrar(
        self, *, end_customer_id, channel, template, status, retry_count=0, last_error=None, notification_id=None
    ):
        return notification_id or uuid.uuid4()


class ObservadorDeIntentos(RegistradorIntentos):
    """Delega en el registrador real y recuerda el estado final del envío (`estado`, `ultimo_error`).

    Por defecto un fallo del registrador real se propaga. Con `ante_fallo` el envío no depende de su registro: el fallo
    se informa a esa función (que no debe loguear datos del cliente) y el envío sigue con un id de reemplazo.
    `cerrar_fallido` deja `failed` la fila de `notifications_log` de un envío que se cortó a medias (p. ej. por plazo),
    para que no quede en `pending`."""

    def __init__(
        self, real: RegistradorIntentos, *, ante_fallo: Callable[[Exception, dict], None] | None = None
    ) -> None:
        self._real = real
        self._ante_fallo = ante_fallo
        self._fija: dict | None = None  # end_customer_id, channel, template
        self._notificacion: uuid.UUID | None = None
        self._reintentos = 0
        self.estado: str | None = None
        self.ultimo_error: str | None = None

    async def registrar(self, **datos):
        self.estado = datos["status"]
        self.ultimo_error = datos.get("last_error")
        self._reintentos = datos.get("retry_count", 0)
        self._fija = {k: datos[k] for k in ("end_customer_id", "channel", "template")}
        try:
            id_ = await self._real.registrar(**datos)
        except Exception as error:  # noqa: BLE001 - se propaga salvo que quien llama pidió tolerarlo
            if self._ante_fallo is None:
                raise
            self._ante_fallo(error, datos)
            id_ = datos.get("notification_id") or uuid.uuid4()
        self._notificacion = datos.get("notification_id") or id_
        return id_

    async def cerrar_fallido(self, motivo: str) -> None:
        """Si hay un intento en `pending`, lo deja `failed` con `motivo`. Sin intento en curso no hace nada."""
        if self.estado != "pending" or self._fija is None:
            return
        await self.registrar(
            **self._fija,
            status="failed",
            retry_count=self._reintentos,
            last_error=motivo,
            notification_id=self._notificacion,
        )
