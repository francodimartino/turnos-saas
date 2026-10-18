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
from dataclasses import dataclass, field
from typing import Any

import httpx
import sentry_sdk
from platform_notifications import (
    BACKOFF_POR_DEFECTO,
    BotAdapter,
    EnvioPedido,
    ErrorCredencial,
    ErrorEnvio,
    RegistradorFuncion,
    RegistradorIntentos,
    adapter_telegram_del_tenant,
    enviar_con_reintentos,
)
from platform_secrets import crear_almacen
from platform_tenancy import tenant_tx
from turnos_negocio import PLANTILLA_VENCIMIENTO, mensaje_vencimiento

log = logging.getLogger("jobs_vencimiento")

# Avisos simultáneos por corrida. Con 5 y los reintentos de 1s/5s/25s, 30 avisos lentos entran de sobra en el
# intervalo de 15 minutos; se cambia con `VENCIMIENTO_CONCURRENCIA` (ver `__main__`).
CONCURRENCIA_POR_DEFECTO = 5
# Tope (segundos) para todo el envío de una corrida: menor que el intervalo de 15 minutos, para que una corrida
# nunca se pise con la siguiente. Lo que no termine se cancela y se cuenta como fallo. `VENCIMIENTO_PLAZO_SEG`.
PLAZO_TOTAL_POR_DEFECTO = 600.0
# Respuestas del proveedor que significan "este token no sirve": no se reintenta ni se prueba con los demás avisos.
ESTADOS_CREDENCIAL_CAIDA = frozenset({401, 403})

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
    omitidos: int = 0  # avisos no intentados porque la credencial de su tenant ya había fallado
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


class _AdapterVigilado(BotAdapter):
    """Delega en el adapter real y recuerda el último `ErrorEnvio` (para distinguir una credencial caída)."""

    def __init__(self, real: BotAdapter) -> None:
        self._real = real
        self.canal = real.canal
        self.ultimo_error: ErrorEnvio | None = None

    async def _vigilar(self, llamada: Awaitable[None]) -> None:
        try:
            await llamada
        except ErrorEnvio as error:
            self.ultimo_error = error
            raise

    async def send_message(self, destinatario: str, texto: str) -> None:
        await self._vigilar(self._real.send_message(destinatario, texto))

    async def send_buttons(self, destinatario, texto, botones) -> None:
        await self._vigilar(self._real.send_buttons(destinatario, texto, botones))

    @staticmethod
    def parsear_update(payload: Any):
        return None


def _credencial_caida(error: ErrorEnvio | None) -> bool:
    """401/403 del proveedor: el token del tenant fue revocado o es inválido; ningún otro envío va a salir."""
    return error is not None and error.estado_http in ESTADOS_CREDENCIAL_CAIDA


@dataclass
class _EstadoTenant:
    """Coordina los avisos de un tenant en la corrida: el primero actúa de sonda y los demás esperan su resultado."""

    primero_reclamado: bool = False
    listo: asyncio.Event = field(default_factory=asyncio.Event)
    adapter: BotAdapter | None = None
    corte: str | None = None  # motivo (sin PII) si la credencial quedó caída: no se intenta más


@dataclass
class _Salida:
    """Qué pasó con un aviso: `ok`, `omitido` (tenant cortado), `corte` (este aviso cortó al tenant) o `fallo`."""

    tipo: str
    motivo: str | None = None


async def _adapter_del_tenant(pool: Any, tenant: Any, cliente_http: httpx.AsyncClient | None) -> BotAdapter:
    async with tenant_tx(pool, tenant) as conn:  # contexto de tenant: solo para leer la credencial
        return await adapter_telegram_del_tenant(crear_almacen(conn), cliente_http)


def _registrador(pool: Any, fila: Any) -> RegistradorIntentos:
    tenant = fila["tenant_id"]
    if fila.get("end_customer_id") is None:
        return _SinRegistro()
    return RegistradorFuncion(lambda: tenant_tx(pool, tenant))


async def _registrar_fallido(pool: Any, fila: Any, motivo: str) -> None:
    """Deja el aviso en `failed` en `notifications_log` sin intentar el envío (solo si hay cliente que registrar)."""
    cliente = fila.get("end_customer_id")
    if cliente is None:
        return
    await _registrador(pool, fila).registrar(
        end_customer_id=cliente, channel="telegram", template=PLANTILLA_VENCIMIENTO, status="failed", last_error=motivo
    )


async def _enviar(
    pool: Any,
    fila: Any,
    adapter: BotAdapter,
    backoff: Sequence[float],
    esperar: Callable[[float], Awaitable[None]],
) -> tuple[str | None, ErrorEnvio | None]:
    """Envía y registra. Devuelve (motivo del fallo o None, último ErrorEnvio del proveedor)."""
    vigilado = _AdapterVigilado(adapter)
    observador = _Observador(_registrador(pool, fila))
    texto = mensaje_vencimiento(fila["starts_at"], fila["service_name"])
    pedido = EnvioPedido(fila.get("end_customer_id"), fila["channel_user_id"], texto, template=PLANTILLA_VENCIMIENTO)
    await enviar_con_reintentos(vigilado, observador, pedido, backoff=backoff, esperar=esperar)
    if observador.estado == "sent":
        return None, None
    return f"envio_fallido: {observador.ultimo_error}"[:200], vigilado.ultimo_error


async def _avisar(
    pool: Any,
    fila: Any,
    estado: _EstadoTenant,
    cupos: asyncio.Semaphore,
    cliente_http: httpx.AsyncClient | None,
    backoff: Sequence[float],
    esperar: Callable[[float], Awaitable[None]],
) -> _Salida:
    """Avisa al cliente de una fila respetando el tope de concurrencia y el corte por credencial caída."""
    tenant = fila["tenant_id"]
    soy_sonda = not estado.primero_reclamado
    estado.primero_reclamado = True
    if not soy_sonda:
        await estado.listo.wait()  # fuera del cupo: no retiene un lugar mientras la sonda trabaja
    async with cupos:
        if estado.corte is not None:
            await _registrar_fallido(pool, fila, estado.corte)
            return _Salida("omitido", estado.corte)
        try:
            if estado.adapter is None:
                estado.adapter = await _adapter_del_tenant(pool, tenant, cliente_http)
            motivo, error = await _enviar(pool, fila, estado.adapter, backoff, esperar)
            if _credencial_caida(error):
                estado.corte = f"credencial_invalida_{error.estado_http}"
                return _Salida("corte", estado.corte)
            return _Salida("ok") if motivo is None else _Salida("fallo", motivo)
        except ErrorCredencial as error_cred:
            estado.corte = type(error_cred).__name__
            await _registrar_fallido(pool, fila, estado.corte)
            return _Salida("corte", estado.corte)
        finally:
            if soy_sonda:
                estado.listo.set()


def _fallo(r: Resultado, reportar: Reportar, descripcion: str, error: Exception) -> None:
    r.fallos += 1
    reportar(descripcion, motivo=type(error).__name__)
    log.error("vencimiento_fallo", extra={"descripcion": descripcion, "motivo": type(error).__name__})


async def _avisar_todos(pool, filas, r, concurrencia, plazo_total, cliente_http, backoff, esperar, reportar) -> None:
    """Avisa en paralelo (hasta `concurrencia` a la vez) y acota el total a `plazo_total` segundos."""
    cupos = asyncio.Semaphore(max(1, concurrencia))
    estados: dict[Any, _EstadoTenant] = {}
    tareas: dict[asyncio.Future, Any] = {}
    for fila in filas:
        estado = estados.setdefault(fila["tenant_id"], _EstadoTenant())
        tarea = asyncio.ensure_future(_avisar(pool, fila, estado, cupos, cliente_http, backoff, esperar))
        tareas[tarea] = fila
    if not tareas:
        return
    _, sin_terminar = await asyncio.wait(tareas, timeout=plazo_total)
    for tarea in sin_terminar:
        tarea.cancel()
    if sin_terminar:
        await asyncio.gather(*sin_terminar, return_exceptions=True)
        r.fallos += len(sin_terminar)
        reportar(
            "se agotó el plazo de la corrida de avisos de vencimiento",
            motivo="plazo_agotado",
            sin_enviar=len(sin_terminar),
        )
        log.error("aviso_vencimiento_plazo_agotado", extra={"motivo": "plazo_agotado", "fallidos": len(sin_terminar)})
    for tarea, fila in tareas.items():
        if tarea in sin_terminar:
            continue
        error = tarea.exception()
        salida = _Salida("fallo", f"error_inesperado: {type(error).__name__}") if error else tarea.result()
        if salida.tipo == "ok":
            r.avisados += 1
        elif salida.tipo == "omitido":
            r.omitidos += 1
        else:
            r.fallos += 1
            descripcion = (
                "la credencial del bot del tenant falló: se omiten sus demás avisos"
                if salida.tipo == "corte"
                else "falló el aviso de vencimiento de un turno"
            )
            reportar(descripcion, motivo=salida.motivo, tenant_id=fila["tenant_id"], booking_id=fila["booking_id"])
            log.error("aviso_vencimiento_fallido", extra={"tenant_id": str(fila["tenant_id"]), "motivo": salida.motivo})


async def ejecutar(
    pool: Any,
    *,
    cliente_http: httpx.AsyncClient | None = None,
    backoff: Sequence[float] = BACKOFF_POR_DEFECTO,
    esperar: Callable[[float], Awaitable[None]] = asyncio.sleep,
    reportar: Reportar = reportar_a_sentry,
    concurrencia: int = CONCURRENCIA_POR_DEFECTO,
    plazo_total: float = PLAZO_TOTAL_POR_DEFECTO,
) -> Resultado:
    """Una corrida del job. `pool` es un pool asyncpg conectado como `scheduler_job`.

    `concurrencia` acota los avisos simultáneos; `plazo_total` (segundos) acota todo el envío de la corrida.
    """
    r = Resultado()
    try:
        async with pool.acquire() as conn:
            filas = await conn.fetch("select * from public.expire_pending_bookings()")
    except Exception as error:  # noqa: BLE001 - sin vencer no hay a quién avisar, pero igual se poda
        _fallo(r, reportar, "no se pudo vencer turnos", error)
        filas = []
    r.vencidos = len(filas)
    con_aviso = [fila for fila in filas if fila["notify"]]
    r.sin_aviso = len(filas) - len(con_aviso)
    await _avisar_todos(pool, con_aviso, r, concurrencia, plazo_total, cliente_http, backoff, esperar, reportar)
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
            "omitidos": r.omitidos,
            "fallos": r.fallos,
            "sesiones_borradas": r.sesiones_borradas,
        },
    )
    return r
