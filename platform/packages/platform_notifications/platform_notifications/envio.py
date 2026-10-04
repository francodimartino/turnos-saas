"""Envío con registro y reintentos (ARCHITECTURE.md §6.4).

Primer intento + hasta 3 reintentos con backoff 1s/5s/25s. La fila de `notifications_log` se crea en
`pending` en el primer intento; entre reintentos sigue `pending` (con `retry_count` y el último error); termina
`sent`, o `failed` si los 3 reintentos también fallan. `programar_envio` lo encola en las `BackgroundTasks` de
FastAPI para que la respuesta HTTP no espere el backoff.

`last_error` guarda solo código/mensaje del proveedor: se le quita cualquier aparición del texto enviado y
los errores no esperados se registran solo por su tipo.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from fastapi import BackgroundTasks

from platform_notifications.adapter import BotAdapter, Boton
from platform_notifications.errores import ErrorEnvio
from platform_notifications.registro import RegistradorIntentos

BACKOFF_POR_DEFECTO: tuple[float, ...] = (1, 5, 25)
_MAX_ERROR = 500


@dataclass(frozen=True)
class EnvioPedido:
    end_customer_id: uuid.UUID
    destinatario: str = field(repr=False)  # identificador del chat: dato del cliente
    texto: str = field(repr=False)
    template: str | None = None
    botones: Sequence[Boton] = ()


def _error_para_registro(error: Exception, texto: str) -> str:
    if isinstance(error, ErrorEnvio):
        mensaje = str(error)
        if texto:
            mensaje = mensaje.replace(texto, "[texto omitido]")
    else:
        mensaje = f"interno: {type(error).__name__}"
    return mensaje[:_MAX_ERROR]


async def _enviar_una_vez(adapter: BotAdapter, pedido: EnvioPedido) -> None:
    if pedido.botones:
        await adapter.send_buttons(pedido.destinatario, pedido.texto, pedido.botones)
    else:
        await adapter.send_message(pedido.destinatario, pedido.texto)


async def enviar_con_reintentos(
    adapter: BotAdapter,
    registrador: RegistradorIntentos,
    pedido: EnvioPedido,
    *,
    backoff: Sequence[float] = BACKOFF_POR_DEFECTO,
    esperar: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> uuid.UUID:
    """Envía y registra. Devuelve el id de la fila de `notifications_log`. No levanta por fallos de envío."""
    datos = {"end_customer_id": pedido.end_customer_id, "channel": adapter.canal, "template": pedido.template}
    notificacion = await registrador.registrar(**datos, status="pending", retry_count=0)
    reintentos = 0
    while True:
        try:
            await _enviar_una_vez(adapter, pedido)
        except Exception as error:  # noqa: BLE001 - cualquier fallo del envío entra al ciclo de reintentos
            ultimo_error = _error_para_registro(error, pedido.texto)
        else:
            await registrador.registrar(**datos, status="sent", retry_count=reintentos, notification_id=notificacion)
            return notificacion
        if reintentos >= len(backoff):
            await registrador.registrar(
                **datos, status="failed", retry_count=reintentos, last_error=ultimo_error, notification_id=notificacion
            )
            return notificacion
        await registrador.registrar(
            **datos, status="pending", retry_count=reintentos, last_error=ultimo_error, notification_id=notificacion
        )
        await esperar(backoff[reintentos])
        reintentos += 1


def programar_envio(
    tareas: BackgroundTasks,
    adapter: BotAdapter,
    registrador: RegistradorIntentos,
    pedido: EnvioPedido,
    *,
    backoff: Sequence[float] = BACKOFF_POR_DEFECTO,
    esperar: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Encola el envío con reintentos en las `BackgroundTasks` de la request: corre después de responder."""
    tareas.add_task(enviar_con_reintentos, adapter, registrador, pedido, backoff=backoff, esperar=esperar)
