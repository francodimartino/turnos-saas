"""Avisos al cliente por los cambios de un turno, sin FastAPI (ARCHITECTURE.md §7.1, §2.6).

Dos etapas, como el resto de la gestión de turnos. `encolar_aviso_del_turno` corre DENTRO de la transacción del cambio
(aprobar, rechazar, cancelar, mover y resolver un cambio de horario pendiente): deja la fila en el outbox
(`notification_outbox`) para toda ficha vigente con algún canal verificado, así que un rollback no deja aviso.
`avisar` y `avisar_liberados` corren DESPUÉS del COMMIT y envían al instante solo si la ficha tiene como único canal
verificado un Telegram: resuelven el bot del tenant, programan el envío en segundo plano con reintentos y registro en
`notifications_log`, y la fila pasa a `sent` o `failed`. Con cualquier otro canal verificado (email, WhatsApp, un
segundo Telegram) no se envía ni se cierra la fila: queda `pending` con primer intento inmediato y hoy ningún proceso
la envía (la respuesta dice `pendiente_de_envio`). Si el proceso muere antes de enviar, la fila de un único Telegram
queda `pending` y la retoma el reescaneo del job de vencimiento, salvo la de `cambio_horario_aprobado` y
`cambio_horario_rechazado`, que el reescaneo no toma: si su envío inmediato no se ejecuta (el proceso muere, o falla el
bot del tenant y la respuesta dice `error`) la fila queda `pending` y hoy ningún proceso la retoma. La regla es
`turnos_dominio.encolar_aviso_segun_canales`. Los textos del envío inmediato son los de `turnos.texto_aviso`. Desde
acá se encolan los avisos `cambio_horario_aprobado` y `cambio_horario_rechazado`; los avisos al negocio no.

Las funciones posteriores al COMMIT nunca lanzan: el cambio ya está confirmado y un fallo del aviso solo se informa.
`tareas` es el administrador de tareas en segundo plano del framework (cualquier objeto con `add_task`).
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
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
    programar_envio,
)
from platform_notifications.outbox import completar_sin_lanzar
from platform_secrets import crear_almacen
from platform_tenancy import tenant_tx
from turnos_dominio import AvisoLiberado, encolar_aviso_segun_canales, pedido_de_envio

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
    # Chat de Telegram al que se envía al instante; solo está si la ficha tiene un único canal verificado, un Telegram.
    destinatario: str | None = field(default=None, repr=False)


SIN_AVISO = AvisoEncolado("no_aplica")


def abrir_tx(pool: Any, sesion: SesionDashboard) -> Any:
    """Transacción corta propia con el tenant y el actor de la sesión (para las etapas posteriores al COMMIT)."""
    return tenant_tx(pool, sesion.tenant_id, sesion.user_id)


async def encolar_aviso_del_turno(conn: Any, fila: Any, plantilla: str | None, inicio: datetime) -> AvisoEncolado:
    """Encola el aviso en la transacción de `conn`, para toda ficha vigente con algún canal verificado.

    Sin plantilla, con la ficha anonimizada o sin canal verificado no encola. Lo encolado lleva solo el turno (id e
    inicio). Con un único canal verificado, un Telegram, se envía al instante tras el COMMIT; con cualquier otro
    canal la fila queda `pending` y no se envía ni se cierra (`turnos_dominio.encolar_aviso_segun_canales`).
    """
    if plantilla is None:
        return SIN_AVISO
    if fila["end_customer_id"] is None:
        return AvisoEncolado("sin_canal")
    aviso = await encolar_aviso_segun_canales(
        conn, end_customer_id=fila["end_customer_id"], plantilla=plantilla, booking_id=fila["id"], inicio=inicio
    )
    return AvisoEncolado(aviso.estado, aviso.outbox_id, aviso.destinatario_telegram)


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
    if aviso.destinatario is None:
        return "pendiente_de_envio"  # fila encolada sin envío inmediato: queda `pending`
    completador = CompletadorOutboxFuncion(lambda: abrir_tx(pool, sesion))
    try:
        async with abrir_tx(pool, sesion) as conn:
            adapter = await opciones.adaptador(conn)
    except ErrorCredencial:
        await completar_sin_lanzar(completador, aviso.outbox_id, "failed", "sin_integracion")
        return "sin_integracion"
    except Exception as exc:  # noqa: BLE001 - sin cerrar: el reescaneo no retoma los cambio_horario_*; otras plantillas, solo de un único Telegram
        _log.error("aviso_sin_adapter", extra={"error": type(exc).__name__})
        return "error"
    pedido = EnvioPedido(
        end_customer_id=fila["end_customer_id"],
        destinatario=aviso.destinatario,
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
    """Programa el envío (post-COMMIT) del aviso de vencimiento a cada cliente liberado con un único Telegram.

    Nunca lanza.

    Los avisos ya están encolados en el outbox, en la transacción del alta o del movimiento. Sin integración la
    fila queda `failed`; ante cualquier otro error queda `pending` (el reescaneo del job de vencimiento solo la
    retoma si la ficha tiene como único canal verificado un Telegram; si no, ningún proceso la envía).
    """
    avisos = [a for a in avisos if a.envio_inmediato]  # los demás quedan `pending`: ni se envían ni se cierran
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
    except Exception as exc:  # noqa: BLE001 - sin cerrar: el reescaneo solo retoma fichas con único Telegram
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
