"""Envío con registro y reintentos (ARCHITECTURE.md §6.4).

Primer intento + hasta 3 reintentos con backoff 1s/5s/25s. Un 4xx distinto de 429 (p. ej. 403 por bot
bloqueado) es permanente: la fila pasa a `failed` en el primer intento. Un 429 espera el `retry_after` de
Telegram (acotado por `tope_retry_after`) en lugar del backoff. Un timeout se reintenta aunque Telegram
pudiera haber recibido el mensaje: se acepta un posible duplicado antes que perder el aviso.

La fila de `notifications_log` se crea en `pending` en el primer intento; entre reintentos sigue `pending`
(con `retry_count` y el último error); termina `sent`, o `failed` si los 3 reintentos también fallan.
Con `outbox` y `pedido.outbox_id` también cierra la fila del outbox (`sent`/`failed`); si el proceso muere antes
queda `pending` para el reescaneo (`outbox.py`). `programar_envio` lo entrega al programador que inyecta
quien llama (p. ej. `BackgroundTasks.add_task`) para que la respuesta HTTP no espere el backoff; el paquete
no conoce ningún framework web.

`last_error` guarda solo código/mensaje del proveedor: se le quita cualquier aparición del texto enviado y
los errores no esperados se registran solo por su tipo.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import TypedDict

from platform_notifications.adapter import BotAdapter, Boton
from platform_notifications.errores import ErrorEnvio
from platform_notifications.outbox import CompletadorOutbox, completar_sin_lanzar
from platform_notifications.registro import RegistradorIntentos


class _DatosIntento(TypedDict):
    """Campos que no cambian entre intentos de un mismo envío (los `**datos` de `registrar`)."""

    end_customer_id: uuid.UUID
    channel: str
    template: str | None


BACKOFF_POR_DEFECTO: tuple[float, ...] = (1, 5, 25)
# Tope de la espera pedida por el proveedor en un 429 (`retry_after`): el envío corre en segundo plano y
# no debe quedar dormido minutos por un valor enorme. Configurable por llamada.
TOPE_RETRY_AFTER_POR_DEFECTO: float = 60
_MAX_ERROR = 500


@dataclass(frozen=True)
class EnvioPedido:
    end_customer_id: uuid.UUID
    destinatario: str = field(repr=False)  # identificador del chat: dato del cliente
    texto: str = field(repr=False)
    template: str | None = None
    botones: Sequence[Boton] = ()
    outbox_id: uuid.UUID | None = None  # fila del outbox a cerrar tras el envío (None = sin outbox)


def _error_para_registro(error: Exception, texto: str) -> str:
    if isinstance(error, ErrorEnvio):
        mensaje = str(error)
        if texto:
            mensaje = mensaje.replace(texto, "[texto omitido]")
    else:
        mensaje = f"interno: {type(error).__name__}"
    return mensaje[:_MAX_ERROR]


def _espera(error: Exception, backoff: Sequence[float], reintentos: int, tope: float) -> float:
    """Segundos antes del próximo intento: el `retry_after` de un 429 (acotado) o el backoff fijo."""
    if isinstance(error, ErrorEnvio) and error.estado_http == 429 and error.retry_after is not None:
        return min(error.retry_after, tope)
    return backoff[reintentos] if reintentos < len(backoff) else 0


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
    tope_retry_after: float = TOPE_RETRY_AFTER_POR_DEFECTO,
    outbox: CompletadorOutbox | None = None,
) -> uuid.UUID:
    """Envía y registra. Devuelve el id de la fila de `notifications_log`. No levanta por fallos de envío."""
    datos: _DatosIntento = {
        "end_customer_id": pedido.end_customer_id,
        "channel": adapter.canal,
        "template": pedido.template,
    }
    notificacion = await registrador.registrar(**datos, status="pending", retry_count=0)
    reintentos = 0
    while True:
        try:
            await _enviar_una_vez(adapter, pedido)
        except Exception as error:  # noqa: BLE001 - cualquier fallo del envío entra al ciclo de reintentos
            ultimo_error = _error_para_registro(error, pedido.texto)
            permanente = isinstance(error, ErrorEnvio) and not error.reintentable
            espera = _espera(error, backoff, reintentos, tope_retry_after)
        else:
            await registrador.registrar(**datos, status="sent", retry_count=reintentos, notification_id=notificacion)
            await completar_sin_lanzar(outbox, pedido.outbox_id, "sent")
            return notificacion
        if permanente or reintentos >= len(backoff):
            await registrador.registrar(
                **datos, status="failed", retry_count=reintentos, last_error=ultimo_error, notification_id=notificacion
            )
            await completar_sin_lanzar(outbox, pedido.outbox_id, "failed", ultimo_error)
            return notificacion
        await registrador.registrar(
            **datos, status="pending", retry_count=reintentos, last_error=ultimo_error, notification_id=notificacion
        )
        await esperar(espera)
        reintentos += 1


def programar_envio(
    programar: Callable[..., object],
    adapter: BotAdapter,
    registrador: RegistradorIntentos,
    pedido: EnvioPedido,
    *,
    backoff: Sequence[float] = BACKOFF_POR_DEFECTO,
    esperar: Callable[[float], Awaitable[None]] = asyncio.sleep,
    tope_retry_after: float = TOPE_RETRY_AFTER_POR_DEFECTO,
    outbox: CompletadorOutbox | None = None,
) -> None:
    """Entrega el envío con reintentos a `programar`, un callable que agenda `f(*args, **kw)` para después.

    Recibe el programador (p. ej. `BackgroundTasks.add_task`) en lugar de las `BackgroundTasks`: lo usan dos
    apps (bot y dashboard), por eso no vive en una sola y el paquete no conoce ningún framework web.
    """
    programar(
        enviar_con_reintentos,
        adapter,
        registrador,
        pedido,
        backoff=backoff,
        esperar=esperar,
        tope_retry_after=tope_retry_after,
        outbox=outbox,
    )
