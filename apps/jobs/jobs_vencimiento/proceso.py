"""Vencimiento de turnos `pending` y poda de sesiones del bot (ARCHITECTURE.md §5.4.1, §3.2, §6.1).

`scheduler_job` no tiene grants sobre ninguna tabla: todo pasa por funciones SECURITY DEFINER
(`expire_pending_bookings`, `prune_bot_sessions`, `find_integration`, `get_integration_secret`,
`record_notification_attempt`). Este módulo no ejecuta SQL propio sobre tablas.

Orden: 1) vencer (la cancelación queda confirmada aunque algo falle después), 2) avisar fila por fila,
aislando los fallos (uno no corta el lote), 3) podar sesiones. Cualquier fallo se reporta sin datos
personales (solo `tenant_id`/`booking_id` como etiquetas) y hace que el job termine con código 1, para que
Render envíe el email de fallo (§6.2).

El aviso se manda fuera de la transacción donde se lee el secret: el backoff de los reintentos no retiene
una conexión.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

import httpx
import sentry_sdk
from platform_notifications import (
    BACKOFF_POR_DEFECTO,
    EnvioPedido,
    ErrorCredencial,
    RegistradorFuncion,
    RegistradorIntentos,
    adapter_telegram_del_tenant,
    enviar_con_reintentos,
)
from platform_secrets import crear_almacen
from platform_tenancy import tenant_tx

log = logging.getLogger("jobs_vencimiento")

# Sin datos del cliente ni del turno: el texto no lleva PII.
MENSAJE_VENCIMIENTO = (
    "Tu solicitud de turno venció sin respuesta del negocio y fue cancelada. Podés volver a reservar cuando quieras."
)
PLANTILLA = "turno_vencido"

Reportar = Callable[..., None]


def reportar_a_sentry(descripcion: str, **etiquetas: Any) -> None:
    """Reporta un fallo a Sentry con etiquetas (ids internos), nunca datos del cliente."""
    with sentry_sdk.new_scope() as scope:
        for clave, valor in etiquetas.items():
            scope.set_tag(clave, str(valor))
        sentry_sdk.capture_message(descripcion, level="error")


@dataclass
class Resultado:
    vencidos: int = 0
    avisados: int = 0
    sin_aviso: int = 0
    fallos: int = 0
    sesiones_borradas: int = 0

    @property
    def exit_code(self) -> int:
        return 1 if self.fallos else 0


class _SinRegistro(RegistradorIntentos):
    """Si la fila de vencimiento no trae el id del cliente no se puede registrar en `notifications_log`
    (`record_notification_attempt` lo exige): el aviso se envía igual, sin registro."""

    async def registrar(
        self, *, end_customer_id, channel, template, status, retry_count=0, last_error=None, notification_id=None
    ):
        return notification_id or uuid.uuid4()


class _Observador(RegistradorIntentos):
    """Delega en el registrador real y recuerda el estado final del envío."""

    def __init__(self, real: RegistradorIntentos) -> None:
        self._real = real
        self.estado: str | None = None
        self.ultimo_error: str | None = None

    async def registrar(self, **datos):
        self.estado = datos["status"]
        self.ultimo_error = datos.get("last_error")
        return await self._real.registrar(**datos)


async def _avisar(
    pool: Any,
    fila: Any,
    cliente_http: httpx.AsyncClient | None,
    backoff: Sequence[float],
    esperar: Callable[[float], Awaitable[None]],
) -> str | None:
    """Avisa al cliente de una fila. Devuelve None si salió bien o el motivo del fallo (sin PII)."""
    tenant = fila["tenant_id"]
    async with tenant_tx(pool, tenant) as conn:  # contexto de tenant: solo para leer la credencial
        try:
            adapter = await adapter_telegram_del_tenant(crear_almacen(conn), cliente_http)
        except ErrorCredencial as error:
            return type(error).__name__
    cliente = fila.get("end_customer_id")
    real: RegistradorIntentos = (
        RegistradorFuncion(lambda: tenant_tx(pool, tenant)) if cliente is not None else _SinRegistro()
    )
    observador = _Observador(real)
    pedido = EnvioPedido(cliente, fila["channel_user_id"], MENSAJE_VENCIMIENTO, template=PLANTILLA)
    await enviar_con_reintentos(adapter, observador, pedido, backoff=backoff, esperar=esperar)
    if observador.estado == "sent":
        return None
    return f"envio_fallido: {observador.ultimo_error}"[:200]


def _fallo(r: Resultado, reportar: Reportar, descripcion: str, error: Exception) -> None:
    r.fallos += 1
    reportar(descripcion, motivo=type(error).__name__)
    log.error("vencimiento_fallo", extra={"descripcion": descripcion, "motivo": type(error).__name__})


async def ejecutar(
    pool: Any,
    *,
    cliente_http: httpx.AsyncClient | None = None,
    backoff: Sequence[float] = BACKOFF_POR_DEFECTO,
    esperar: Callable[[float], Awaitable[None]] = asyncio.sleep,
    reportar: Reportar = reportar_a_sentry,
) -> Resultado:
    """Una corrida del job. `pool` es un pool asyncpg conectado como `scheduler_job`."""
    r = Resultado()
    try:
        async with pool.acquire() as conn:
            filas = await conn.fetch("select * from public.expire_pending_bookings()")
    except Exception as error:  # noqa: BLE001 - sin vencer no hay a quién avisar, pero igual se poda
        _fallo(r, reportar, "no se pudo vencer turnos", error)
        filas = []
    r.vencidos = len(filas)
    for fila in filas:
        if not fila["notify"]:
            r.sin_aviso += 1
            continue
        try:
            motivo = await _avisar(pool, fila, cliente_http, backoff, esperar)
        except Exception as error:  # noqa: BLE001 - un tenant/cliente con problemas no corta el lote
            motivo = f"error_inesperado: {type(error).__name__}"
        if motivo is None:
            r.avisados += 1
            continue
        r.fallos += 1
        reportar(
            "falló el aviso de vencimiento de un turno",
            motivo=motivo,
            tenant_id=fila["tenant_id"],
            booking_id=fila["booking_id"],
        )
        log.error("aviso_vencimiento_fallido", extra={"tenant_id": str(fila["tenant_id"]), "motivo": motivo})
    try:
        async with pool.acquire() as conn:
            r.sesiones_borradas = int(await conn.fetchval("select public.prune_bot_sessions()") or 0)
    except Exception as error:  # noqa: BLE001
        _fallo(r, reportar, "no se pudo podar sesiones del bot", error)
    log.info(
        "vencimiento_terminado",
        extra={
            "vencidos": r.vencidos,
            "avisados": r.avisados,
            "sin_aviso": r.sin_aviso,
            "fallos": r.fallos,
            "sesiones_borradas": r.sesiones_borradas,
        },
    )
    return r
