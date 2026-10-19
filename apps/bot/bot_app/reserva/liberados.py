"""Aviso de vencimiento a los clientes cuyo turno `pending` vencido se liberó al reservar sobre su horario.

El turno liberado queda `cancelled`, así que `expire_pending_bookings()` (el job) ya no lo encuentra: el aviso lo
manda el bot con las filas que devolvió `release_expired_pending_overlaps`, con el mismo texto y la misma
plantilla (`turno_vencido`) que el job (ambos desde `platform_notifications`), registrado en
`notifications_log`. Cada turno se avisa una sola vez: lo libera la función o lo vence el job, nunca ambos.

Corre DESPUÉS del commit de la reserva (`ctx.despues_del_commit`): una falla de Telegram no afecta la reserva.
Los logs no llevan datos del cliente.
"""

import logging
from collections.abc import Sequence
from functools import partial

import httpx
from platform_notifications import (
    EnvioPedido,
    ErrorCredencial,
    RegistradorFuncion,
    adapter_telegram_del_tenant,
    enviar_con_reintentos,
)
from platform_secrets import crear_almacen
from platform_tenancy import tenant_tx
from turnos_negocio import PLANTILLA_VENCIMIENTO, mensaje_vencimiento

from bot_app.despachador import AccionPosterior, Recursos
from bot_app.reserva.repositorio import Liberado

logger = logging.getLogger("bot.reserva.liberados")


async def _avisar(recursos: Recursos, liberados: Sequence[Liberado], cliente_http: httpx.AsyncClient) -> None:
    for liberado in liberados:
        if not liberado.notify or liberado.end_customer_id is None or liberado.channel_user_id is None:
            continue
        if liberado.starts_at is None or liberado.service_name is None:
            continue  # sin el turno no se puede decir cuál venció
        tenant = liberado.tenant_id
        try:
            async with tenant_tx(recursos.pool, tenant) as conn:  # solo para leer la credencial
                adapter = await adapter_telegram_del_tenant(crear_almacen(conn), cliente_http)
            texto = mensaje_vencimiento(liberado.starts_at, liberado.service_name)
            pedido = EnvioPedido(
                liberado.end_customer_id, liberado.channel_user_id, texto, template=PLANTILLA_VENCIMIENTO
            )
            registrador = RegistradorFuncion(partial(tenant_tx, recursos.pool, tenant))
            await enviar_con_reintentos(adapter, registrador, pedido)
        except ErrorCredencial:
            logger.warning("aviso_liberado_sin_credencial")
        except Exception as error:  # noqa: BLE001 - un aviso fallido no corta los demás ni afecta la reserva
            logger.warning("aviso_liberado_fallido", extra={"error": type(error).__name__})


def avisar_liberados(liberados: Sequence[Liberado]) -> AccionPosterior:
    """Acción para `ctx.despues_del_commit`: avisa el vencimiento a cada cliente liberado con `notify`."""

    async def accion(recursos: Recursos) -> None:
        propio = recursos.cliente_http is None
        cliente = httpx.AsyncClient(timeout=15.0) if propio else recursos.cliente_http
        try:
            await _avisar(recursos, liberados, cliente)
        finally:
            if propio:
                await cliente.aclose()

    return accion
