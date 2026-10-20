"""Envío del aviso de vencimiento a los clientes cuyo turno `pending` vencido se liberó al reservar sobre su horario.

El turno liberado queda `cancelled`, así que el job de vencimiento ya no lo encuentra. El aviso se encola en el
outbox dentro de la transacción de la reserva (`turnos_dominio.avisos.encolar_avisos_liberados`, desde el flujo),
con el mismo texto y la misma plantilla (`turno_vencido`) que el job. Este módulo solo hace el envío inmediato
posterior al commit (`ctx.despues_del_commit`): `enviar_con_reintentos` registra en `notifications_log` y cierra la
fila del outbox (`sent`/`failed`). Si el proceso muere antes, la fila queda `pending` y la reenvía el reescaneo del
job. Una falla de Telegram no afecta la reserva. Los logs no llevan datos del cliente.
"""

import logging
from collections.abc import Sequence
from functools import partial

import httpx
from platform_notifications import (
    CompletadorOutboxFuncion,
    ErrorCredencial,
    RegistradorFuncion,
    adapter_telegram_del_tenant,
    completar_sin_lanzar,
    enviar_con_reintentos,
)
from platform_secrets import crear_almacen
from platform_tenancy import tenant_tx
from turnos_dominio.avisos import AvisoLiberado, pedido_de_envio

from turnos_bot.despachador import AccionPosterior, Recursos

logger = logging.getLogger("bot.reserva.liberados")


async def _enviar(recursos: Recursos, avisos: Sequence[AvisoLiberado], cliente_http: httpx.AsyncClient) -> None:
    for aviso in avisos:
        tenant = aviso.liberado.tenant_id
        completador = CompletadorOutboxFuncion(partial(tenant_tx, recursos.pool, tenant))
        try:
            async with tenant_tx(recursos.pool, tenant) as conn:  # solo para leer la credencial
                adapter = await adapter_telegram_del_tenant(
                    crear_almacen(conn), cliente_http, base_url=recursos.base_url_telegram
                )
            registrador = RegistradorFuncion(partial(tenant_tx, recursos.pool, tenant))
            await enviar_con_reintentos(adapter, registrador, pedido_de_envio(aviso), outbox=completador)
        except ErrorCredencial:
            logger.warning("aviso_liberado_sin_credencial")
            await completar_sin_lanzar(completador, aviso.outbox_id, "failed", "sin_integracion")
        except Exception as error:  # noqa: BLE001 - un aviso fallido no corta los demás ni afecta la reserva
            logger.warning("aviso_liberado_fallido", extra={"error": type(error).__name__})


def enviar_avisos_liberados(avisos: Sequence[AvisoLiberado]) -> AccionPosterior:
    """Acción para `ctx.despues_del_commit`: envía los avisos ya encolados y cierra sus filas del outbox."""

    async def accion(recursos: Recursos) -> None:
        propio = recursos.cliente_http is None
        cliente = httpx.AsyncClient(timeout=15.0) if propio else recursos.cliente_http
        try:
            await _enviar(recursos, avisos, cliente)
        finally:
            if propio:
                await cliente.aclose()

    return accion
