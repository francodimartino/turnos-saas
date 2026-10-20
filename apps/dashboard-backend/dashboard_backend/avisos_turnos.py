"""Avisos al cliente por los cambios de un turno, sin FastAPI (ARCHITECTURE.md §7.1, §2.6).

Dos etapas, como el resto de la gestión de turnos. `encolar_aviso_del_turno` corre DENTRO de la transacción del
cambio: deja la fila en el outbox (`notification_outbox`), así que un rollback no deja aviso. `avisar` y
`avisar_liberados` corren DESPUÉS del COMMIT: resuelven el bot del tenant, programan el envío en segundo plano con
reintentos y registro en `notifications_log`, y la fila del outbox pasa a `sent` o `failed`. Si el proceso muere
antes de enviar, la fila queda `pending` y el reescaneo del job de vencimiento la envía.

Las funciones posteriores al COMMIT nunca lanzan: el cambio ya está confirmado y un fallo del aviso solo se informa.
`tareas` es el administrador de tareas en segundo plano del framework (cualquier objeto con `add_task`).
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from platform_auth import SesionDashboard
from platform_notifications import (
    BACKOFF_POR_DEFECTO,
    BASE_URL_OFICIAL,
    BotAdapter,
    CompletadorOutboxFuncion,
    EnvioPedido,
    ErrorCredencial,
    RegistradorFuncion,
    adapter_telegram_del_tenant,
    encolar_aviso,
    programar_envio,
)
from platform_notifications.outbox import completar_sin_lanzar
from platform_secrets import crear_almacen
from platform_tenancy import tenant_tx
from turnos_dominio import AvisoLiberado, pedido_de_envio

from dashboard_backend import turnos as t

_log = logging.getLogger("dashboard_backend.turnos")

FabricaAdaptador = Callable[[Any, "httpx.AsyncClient | None"], Awaitable[BotAdapter]]


def fabrica_adaptador_telegram(base_url: str = BASE_URL_OFICIAL) -> FabricaAdaptador:
    """Fábrica del bot del tenant del contexto de la transacción (`find_integration` + secret de la integración)."""

    async def fabrica(conn: Any, cliente: httpx.AsyncClient | None) -> BotAdapter:
        return await adapter_telegram_del_tenant(crear_almacen(conn), cliente, base_url=base_url)

    return fabrica


@dataclass
class OpcionesAvisos:
    """Cómo se avisa al cliente. Los valores por defecto son los de producción; los tests inyectan los suyos."""

    cliente_http: httpx.AsyncClient | None = None
    # Base de la Bot API (decidida al arrancar); solo se usa si no se inyecta `fabrica_adaptador`.
    base_url_telegram: str = BASE_URL_OFICIAL
    fabrica_adaptador: FabricaAdaptador | None = None
    backoff: Sequence[float] = BACKOFF_POR_DEFECTO
    esperar: Callable[[float], Awaitable[None]] = asyncio.sleep

    async def adaptador(self, conn: Any) -> BotAdapter:
        """Bot del tenant del contexto de `conn`: el inyectado, o el de Telegram con la base configurada al arrancar."""
        fabrica = self.fabrica_adaptador or fabrica_adaptador_telegram(self.base_url_telegram)
        return await fabrica(conn, self.cliente_http)


@dataclass(frozen=True)
class AvisoEncolado:
    """Resultado de encolar el aviso dentro de la transacción del cambio: `estado` es lo que ve el llamador."""

    estado: str
    outbox_id: uuid.UUID | None = None


SIN_AVISO = AvisoEncolado("no_aplica")


def abrir_tx(pool: Any, sesion: SesionDashboard) -> Any:
    """Transacción corta propia con el tenant y el actor de la sesión (para las etapas posteriores al COMMIT)."""
    return tenant_tx(pool, sesion.tenant_id, sesion.user_id)


async def encolar_aviso_del_turno(conn: Any, fila: Any, plantilla: str | None, inicio: datetime) -> AvisoEncolado:
    """Encola el aviso en la transacción de `conn`. Sin plantilla, cliente anonimizado o canal sin soporte, no encola.

    `params` lleva solo identificadores y el inicio del turno: el texto y el destinatario se resuelven al enviar.
    """
    if plantilla is None:
        return SIN_AVISO
    if fila["customer_deleted_at"] is not None:
        return AvisoEncolado("cliente_anonimizado")
    if not fila["customer_channel"] or not fila["customer_channel_user_id"]:
        return AvisoEncolado("sin_canal")
    if fila["customer_channel"] != "telegram":
        return AvisoEncolado("canal_no_soportado")
    outbox_id = await encolar_aviso(
        conn,
        end_customer_id=fila["end_customer_id"],
        channel=fila["customer_channel"],
        template=plantilla,
        params={"booking_id": str(fila["id"]), "inicio": inicio.isoformat()},
    )
    if outbox_id is None:  # anonimizado entre la lectura y el encolado
        return AvisoEncolado("cliente_anonimizado")
    return AvisoEncolado("encolado", outbox_id)


async def avisar(
    pool: Any,
    sesion: SesionDashboard,
    tareas: Any,
    opciones: OpcionesAvisos,
    fila: Any,
    plantilla: str,
    inicio: datetime,
    aviso: AvisoEncolado,
) -> str:
    """Programa el envío del aviso ya encolado (post-COMMIT). Devuelve qué pasó; nunca lanza."""
    if aviso.outbox_id is None:
        return aviso.estado
    completador = CompletadorOutboxFuncion(lambda: abrir_tx(pool, sesion))
    try:
        async with abrir_tx(pool, sesion) as conn:
            adapter = await opciones.adaptador(conn)
    except ErrorCredencial:
        await completar_sin_lanzar(completador, aviso.outbox_id, "failed", "sin_integracion")
        return "sin_integracion"
    except Exception as exc:  # noqa: BLE001 - la fila sigue pending: el reescaneo la reintenta
        _log.error("aviso_sin_adapter", extra={"error": type(exc).__name__})
        return "error"
    pedido = EnvioPedido(
        end_customer_id=fila["end_customer_id"],
        destinatario=fila["customer_channel_user_id"],
        texto=t.texto_aviso(plantilla, fila, inicio),
        template=plantilla,
        outbox_id=aviso.outbox_id,
    )
    registrador = RegistradorFuncion(lambda: abrir_tx(pool, sesion))
    programar_envio(
        tareas.add_task,
        adapter,
        registrador,
        pedido,
        backoff=opciones.backoff,
        esperar=opciones.esperar,
        outbox=completador,
    )
    return "programado"


async def avisar_liberados(
    pool: Any,
    sesion: SesionDashboard,
    tareas: Any,
    opciones: OpcionesAvisos,
    avisos: Sequence[AvisoLiberado],
) -> None:
    """Programa el envío (post-COMMIT) del aviso de vencimiento a cada cliente liberado. Nunca lanza.

    Los avisos ya están encolados en el outbox, en la transacción del alta o del movimiento. Sin integración la
    fila queda `failed`; ante cualquier otro error queda `pending` y el reescaneo del job de vencimiento la envía.
    """
    if not avisos:
        return
    completador = CompletadorOutboxFuncion(lambda: abrir_tx(pool, sesion))
    try:
        async with abrir_tx(pool, sesion) as conn:
            adapter = await opciones.adaptador(conn)
    except ErrorCredencial:
        for aviso in avisos:
            await completar_sin_lanzar(completador, aviso.outbox_id, "failed", "sin_integracion")
        return
    except Exception as exc:  # noqa: BLE001 - las filas siguen pending: el reescaneo las reintenta
        _log.error("aviso_liberados_sin_adapter", extra={"error": type(exc).__name__})
        return
    registrador = RegistradorFuncion(lambda: abrir_tx(pool, sesion))
    for aviso in avisos:
        programar_envio(
            tareas.add_task,
            adapter,
            registrador,
            pedido_de_envio(aviso),
            backoff=opciones.backoff,
            esperar=opciones.esperar,
            outbox=completador,
        )
