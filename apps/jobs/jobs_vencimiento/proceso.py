"""Vencimiento de turnos `pending` y poda de sesiones del bot (ARCHITECTURE.md §5.4.1, §3.2, §6.1).

`scheduler_job` no tiene grants sobre ninguna tabla: todo pasa por funciones SECURITY DEFINER
(`expire_pending_bookings`, `prune_bot_sessions`, `find_integration`, `get_integration_secret`,
`record_notification_attempt`). Este módulo no ejecuta SQL propio sobre tablas.

Orden: 1) vencer (la cancelación queda confirmada aunque algo falle después; el aviso de cada turno se encola
en el outbox en la misma sentencia), 2) avisar fila por fila, aislando los fallos (uno no corta el lote),
3) reescanear el outbox (§6.4): avisos de turno que quedaron `pending` porque el proceso que los encoló murió o el
envío inmediato falló, 4) podar sesiones y filas viejas del outbox. Cualquier fallo se reporta sin datos
personales (solo `tenant_id`/`booking_id` como etiquetas) y hace que el job termine con código 1, para que
Render envíe el email de fallo (§6.2).

El aviso se manda fuera de la transacción donde se lee el secret: el backoff de los reintentos no retiene
una conexión.
"""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TypeGuard, cast

import httpx
import sentry_sdk
from jobs_purga.proceso import ejecutar_con_monitor
from platform_notifications import (
    BACKOFF_POR_DEFECTO,
    BASE_URL_OFICIAL,
    BotAdapter,
    CompletadorOutbox,
    CompletadorOutboxFuncion,
    EnvioPedido,
    ErrorCredencial,
    ErrorEnvio,
    RegistradorFuncion,
    RegistradorIntentos,
    adapter_telegram_del_tenant,
    completar_sin_lanzar,
    enviar_con_reintentos,
)
from platform_secrets import crear_almacen
from platform_tenancy import tenant_tx
from sentry_sdk.crons import capture_checkin
from turnos_negocio import PLANTILLA_VENCIMIENTO, mensaje_aviso_turno, mensaje_vencimiento

log = logging.getLogger("jobs_vencimiento")

# Avisos simultáneos por corrida. Con 5 y los reintentos de 1s/5s/25s, 30 avisos lentos entran de sobra en el
# intervalo de 15 minutos; se cambia con `VENCIMIENTO_CONCURRENCIA` (ver `__main__`).
CONCURRENCIA_POR_DEFECTO = 5
# Tope (segundos) para todo el envío de una corrida: menor que el intervalo de 15 minutos, para que una corrida
# nunca se pise con la siguiente. Lo que no termine se cancela y se cuenta como fallo. `VENCIMIENTO_PLAZO_SEG`.
PLAZO_TOTAL_POR_DEFECTO = 600.0
# Reescaneo del outbox: filas por corrida, arriendo (segundos que una fila reclamada queda fuera del alcance de otra
# corrida: mayor que el plazo de la corrida) y reclamos máximos antes de dejarla `failed`. Se cambian con
# `OUTBOX_LOTE`, `OUTBOX_ARRIENDO_SEG` y `OUTBOX_MAX_INTENTOS` (ver `__main__`). La espera previa al primer intento
# (5 minutos) la fija quien encola.
LOTE_OUTBOX_POR_DEFECTO = 50
ARRIENDO_OUTBOX_POR_DEFECTO = 900
MAX_INTENTOS_OUTBOX_POR_DEFECTO = 5
RETENCION_OUTBOX_DIAS_POR_DEFECTO = 30
# Plazo mínimo del reescaneo aunque el envío de los vencidos haya consumido todo el plazo de la corrida.
_PLAZO_MINIMO_REESCANEO = 30.0
# Respuestas del proveedor que significan "este token no sirve": no se reintenta ni se prueba con los demás avisos.
ESTADOS_CREDENCIAL_CAIDA = frozenset({401, 403})

# Monitor de cron de Sentry (mismo helper que la purga). El horario tiene que coincidir con el schedule del Cron
# Job. Margen y duración máxima acotados al intervalo de 15 minutos (el envío de la corrida dura como mucho 10).
MONITOR_SLUG = "vencimiento-turnos"
CRON_POR_DEFECTO = "*/15 * * * *"
_MARGEN_MINUTOS = 5
_DURACION_MAXIMA_MINUTOS = 12

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
    reescaneadas: int = 0  # filas del outbox reclamadas
    reescaneo_enviadas: int = 0
    reescaneo_reprogramadas: int = 0  # fallo transitorio con intentos disponibles: la retoma otra corrida
    reescaneo_descartadas: int = 0  # sin a quién avisar (turno borrado, tenant inactivo, cliente anonimizado)
    outbox_podadas: int = 0

    @property
    def exit_code(self) -> int:
        return 1 if self.fallos else 0

    @property
    def ok(self) -> bool:
        return not self.fallos


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


def _credencial_caida(error: ErrorEnvio | None) -> TypeGuard[ErrorEnvio]:
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


async def _adapter_del_tenant(
    pool: Any, tenant: Any, cliente_http: httpx.AsyncClient | None, base_url: str = BASE_URL_OFICIAL
) -> BotAdapter:
    async with tenant_tx(pool, tenant) as conn:  # contexto de tenant: solo para leer la credencial
        return await adapter_telegram_del_tenant(crear_almacen(conn), cliente_http, base_url=base_url)


def _registrador(pool: Any, fila: Any) -> RegistradorIntentos:
    tenant = fila["tenant_id"]
    if fila.get("end_customer_id") is None:
        return _SinRegistro()
    return RegistradorFuncion(lambda: tenant_tx(pool, tenant))


def _completador(pool: Any, tenant: Any) -> CompletadorOutbox:
    """Cierra filas del outbox con el contexto de su tenant (`complete_notification_outbox` lo exige)."""
    return CompletadorOutboxFuncion(lambda: tenant_tx(pool, tenant))


async def _registrar_fallido(pool: Any, fila: Any, motivo: str) -> None:
    """Deja el aviso en `failed` (outbox y `notifications_log`) sin intentar el envío."""
    await completar_sin_lanzar(_completador(pool, fila["tenant_id"]), fila.get("outbox_id"), "failed", motivo)
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
    outbox_id = fila.get("outbox_id")
    pedido = EnvioPedido(
        fila.get("end_customer_id"),
        fila["channel_user_id"],
        texto,
        template=PLANTILLA_VENCIMIENTO,
        outbox_id=outbox_id,
    )
    completador = _completador(pool, fila["tenant_id"]) if outbox_id is not None else None
    await enviar_con_reintentos(vigilado, observador, pedido, backoff=backoff, esperar=esperar, outbox=completador)
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
    base_url: str = BASE_URL_OFICIAL,
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
                estado.adapter = await _adapter_del_tenant(pool, tenant, cliente_http, base_url)
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


async def _avisar_todos(
    pool, filas, r, concurrencia, plazo_total, cliente_http, backoff, esperar, reportar, base_url=BASE_URL_OFICIAL
) -> None:
    """Avisa en paralelo (hasta `concurrencia` a la vez) y acota el total a `plazo_total` segundos."""
    cupos = asyncio.Semaphore(max(1, concurrencia))
    estados: dict[Any, _EstadoTenant] = {}
    tareas: dict[asyncio.Future, Any] = {}
    for fila in filas:
        estado = estados.setdefault(fila["tenant_id"], _EstadoTenant())
        tarea: asyncio.Future[_Salida] = asyncio.ensure_future(
            _avisar(pool, fila, estado, cupos, cliente_http, backoff, esperar, base_url)
        )
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
        excepcion = tarea.exception()
        salida = _Salida("fallo", f"error_inesperado: {type(excepcion).__name__}") if excepcion else tarea.result()
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


# --- Reescaneo del outbox (§6.4) -------------------------------------------------------------------------------------


@dataclass
class _SalidaOutbox:
    """Qué pasó con una fila reclamada: `enviada`, `reprogramada`, `descartada` (sin a quién avisar), `cerrada`
    (otro proceso la cerró antes) o `fallo`."""

    tipo: str
    motivo: str | None = None


@dataclass
class _TenantOutbox:
    """Un adapter por tenant y corrida: las filas del mismo tenant comparten la lectura de la credencial."""

    candado: asyncio.Lock = field(default_factory=asyncio.Lock)
    adapter: BotAdapter | None = None
    error: str | None = None  # motivo (sin PII) si el tenant no tiene credencial usable


class _CompletadorReescaneo(CompletadorOutbox):
    """Cierra la fila con la política del reescaneo: un fallo transitorio con intentos disponibles la deja `pending`
    para otra corrida; un error permanente del proveedor o el último intento la dejan `failed`."""

    def __init__(self, real: CompletadorOutbox, vigilado: _AdapterVigilado, intentos: int, maximo: int, reintento: int):
        self._real = real
        self._vigilado = vigilado
        self._intentos = intentos
        self._maximo = maximo
        self._reintento = reintento
        self.reprogramada = False

    async def completar(self, outbox_id, status, last_error=None, retry_in_seconds=None):
        error = self._vigilado.ultimo_error
        permanente = error is not None and not error.reintentable
        if status == "failed" and not permanente and self._intentos < self._maximo:
            self.reprogramada = True
            return await self._real.completar(outbox_id, "pending", last_error, self._reintento)
        return await self._real.completar(outbox_id, status, last_error, retry_in_seconds)


def _parametros(fila: Any) -> dict[str, Any]:
    crudo = fila["params"]
    datos = json.loads(crudo) if isinstance(crudo, str) else crudo
    return datos if isinstance(datos, dict) else {}


def _inicio_aviso(fila: Any, destino: Any) -> datetime:
    """El inicio con que se encoló el aviso (un turno movido ya no empieza donde empezaba); si falta, el del turno."""
    try:
        return datetime.fromisoformat(str(_parametros(fila)["inicio"]))
    except (KeyError, ValueError):
        return destino["starts_at"]


async def _cerrar(completador: CompletadorOutbox, fila: Any, status: str, motivo: str) -> bool:
    """Cierra la fila sin propagar fallos. `False` si no se pudo o si ya no estaba `pending`."""
    try:
        return bool(await completador.completar(fila["id"], status, motivo))
    except Exception as error:  # noqa: BLE001 - queda pending: la retoma otra corrida
        log.warning("outbox_sin_cerrar", extra={"motivo": type(error).__name__})
        return False


async def _destino_de(pool: Any, fila: Any) -> Any:
    async with tenant_tx(pool, fila["tenant_id"]) as conn:
        return await conn.fetchrow("select * from public.resolve_outbox_delivery($1)", fila["id"])


async def _adapter_outbox(
    pool: Any,
    tenant: Any,
    estado: _TenantOutbox,
    cliente_http: httpx.AsyncClient | None,
    base_url: str = BASE_URL_OFICIAL,
):
    async with estado.candado:
        if estado.adapter is None and estado.error is None:
            try:
                estado.adapter = await _adapter_del_tenant(pool, tenant, cliente_http, base_url)
            except ErrorCredencial:
                estado.error = "sin_integracion"
    return estado.adapter, estado.error


async def _reenviar(
    pool: Any,
    fila: Any,
    estado: _TenantOutbox,
    cliente_http: httpx.AsyncClient | None,
    backoff: Sequence[float],
    esperar: Callable[[float], Awaitable[None]],
    max_intentos: int,
    reintento: int,
    base_url: str = BASE_URL_OFICIAL,
) -> _SalidaOutbox:
    """Resuelve el turno de la fila, arma el texto y lo envía con la política de reintentos del envío inmediato."""
    tenant = fila["tenant_id"]
    real = _completador(pool, tenant)
    destino = await _destino_de(pool, fila)
    descarte = None
    if destino is None:
        descarte = "turno_inexistente"
    elif not destino["tenant_active"]:
        descarte = "tenant_inactivo"
    elif destino["channel_user_id"] is None:
        descarte = "cliente_sin_destinatario"
    texto = None
    if descarte is None:
        texto = mensaje_aviso_turno(
            fila["template"],
            servicio=destino["service_name"],
            profesional=destino["professional_name"],
            inicio=_inicio_aviso(fila, destino),
            vence=destino["pending_expires_at"],
        )
        if texto is None:
            descarte = "plantilla_desconocida"
    if descarte is not None:
        return _SalidaOutbox("descartada" if await _cerrar(real, fila, "failed", descarte) else "cerrada", descarte)
    texto = cast(str, texto)  # sin descarte, el texto siempre se armó arriba
    adapter, sin_credencial = await _adapter_outbox(pool, tenant, estado, cliente_http, base_url)
    if adapter is None:
        await _cerrar(real, fila, "failed", sin_credencial or "sin_integracion")
        return _SalidaOutbox("fallo", sin_credencial)
    vigilado = _AdapterVigilado(adapter)
    observador = _Observador(_registrador(pool, fila))
    completador = _CompletadorReescaneo(real, vigilado, fila["attempts"], max_intentos, reintento)
    pedido = EnvioPedido(
        fila["end_customer_id"], destino["channel_user_id"], texto, template=fila["template"], outbox_id=fila["id"]
    )
    await enviar_con_reintentos(vigilado, observador, pedido, backoff=backoff, esperar=esperar, outbox=completador)
    if observador.estado == "sent":
        return _SalidaOutbox("enviada")
    motivo = f"envio_fallido: {observador.ultimo_error}"[:200]
    return _SalidaOutbox("reprogramada" if completador.reprogramada else "fallo", motivo)


async def _reescanear_outbox(
    pool: Any,
    r: Resultado,
    *,
    cliente_http: httpx.AsyncClient | None,
    backoff: Sequence[float],
    esperar: Callable[[float], Awaitable[None]],
    reportar: Reportar,
    concurrencia: int,
    plazo: float,
    lote: int,
    arriendo: int,
    max_intentos: int,
    base_url: str = BASE_URL_OFICIAL,
) -> None:
    """Envía los avisos `pending` del outbox cuya espera ya venció. Reclamar es atómico (SKIP LOCKED + arriendo):
    dos corridas simultáneas nunca reciben la misma fila, y las que quedan sin cerrar (el job murió o se agotó el
    plazo) vuelven a estar disponibles al vencer el arriendo. Al agotar `max_intentos` reclamos pasan a `failed`."""
    try:
        async with pool.acquire() as conn:
            filas = await conn.fetch(
                "select * from public.claim_notification_outbox($1, $2, $3)", lote, arriendo, max_intentos
            )
    except Exception as error:  # noqa: BLE001 - sin reclamo no hay reescaneo, pero el resto de la corrida sigue
        _fallo(r, reportar, "no se pudo reescanear el outbox de avisos", error)
        return
    r.reescaneadas = len(filas)
    if not filas:
        return
    cupos = asyncio.Semaphore(max(1, concurrencia))
    estados: dict[Any, _TenantOutbox] = {}

    async def una(fila: Any) -> _SalidaOutbox:
        async with cupos:
            return await _reenviar(
                pool,
                fila,
                estados.setdefault(fila["tenant_id"], _TenantOutbox()),
                cliente_http,
                backoff,
                esperar,
                max_intentos,
                arriendo,
                base_url,
            )

    tareas = {asyncio.ensure_future(una(fila)): fila for fila in filas}
    _, sin_terminar = await asyncio.wait(tareas, timeout=plazo)
    for tarea in sin_terminar:
        tarea.cancel()
    if sin_terminar:
        await asyncio.gather(*sin_terminar, return_exceptions=True)
        r.fallos += len(sin_terminar)
        reportar("se agotó el plazo del reescaneo del outbox", motivo="plazo_agotado", sin_enviar=len(sin_terminar))
    for tarea, fila in tareas.items():
        if tarea in sin_terminar:
            continue
        excepcion = tarea.exception()
        salida = (
            _SalidaOutbox("fallo", f"error_inesperado: {type(excepcion).__name__}") if excepcion else tarea.result()
        )
        if salida.tipo == "enviada":
            r.reescaneo_enviadas += 1
        elif salida.tipo == "reprogramada":
            r.reescaneo_reprogramadas += 1
            log.warning("outbox_reprogramada", extra={"tenant_id": str(fila["tenant_id"]), "motivo": salida.motivo})
        elif salida.tipo == "descartada":
            r.reescaneo_descartadas += 1
        elif salida.tipo == "fallo":
            r.fallos += 1
            reportar("falló un aviso del outbox", motivo=salida.motivo, tenant_id=fila["tenant_id"])
            log.error("outbox_fallo", extra={"tenant_id": str(fila["tenant_id"]), "motivo": salida.motivo})


async def ejecutar(
    pool: Any,
    *,
    cliente_http: httpx.AsyncClient | None = None,
    backoff: Sequence[float] = BACKOFF_POR_DEFECTO,
    esperar: Callable[[float], Awaitable[None]] = asyncio.sleep,
    reportar: Reportar = reportar_a_sentry,
    concurrencia: int = CONCURRENCIA_POR_DEFECTO,
    plazo_total: float = PLAZO_TOTAL_POR_DEFECTO,
    lote_outbox: int = LOTE_OUTBOX_POR_DEFECTO,
    arriendo_outbox: int = ARRIENDO_OUTBOX_POR_DEFECTO,
    max_intentos_outbox: int = MAX_INTENTOS_OUTBOX_POR_DEFECTO,
    retencion_outbox_dias: int = RETENCION_OUTBOX_DIAS_POR_DEFECTO,
    base_url_telegram: str = BASE_URL_OFICIAL,
) -> Resultado:
    """Una corrida del job. `pool` es un pool asyncpg conectado como `scheduler_job`.

    `concurrencia` acota los avisos simultáneos; `plazo_total` (segundos) acota todo el envío de la corrida
    (avisos de vencimiento y reescaneo del outbox juntos).
    """
    inicio = time.monotonic()
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
    await _avisar_todos(
        pool, con_aviso, r, concurrencia, plazo_total, cliente_http, backoff, esperar, reportar, base_url_telegram
    )
    await _reescanear_outbox(
        pool,
        r,
        cliente_http=cliente_http,
        backoff=backoff,
        esperar=esperar,
        reportar=reportar,
        concurrencia=concurrencia,
        plazo=max(_PLAZO_MINIMO_REESCANEO, plazo_total - (time.monotonic() - inicio)),
        lote=lote_outbox,
        arriendo=arriendo_outbox,
        max_intentos=max_intentos_outbox,
        base_url=base_url_telegram,
    )
    try:
        async with pool.acquire() as conn:
            r.sesiones_borradas = int(await conn.fetchval("select public.prune_bot_sessions()") or 0)
    except Exception as error:  # noqa: BLE001
        _fallo(r, reportar, "no se pudo podar sesiones del bot", error)
    try:
        async with pool.acquire() as conn:
            r.outbox_podadas = int(
                await conn.fetchval("select public.prune_notification_outbox($1)", retencion_outbox_dias) or 0
            )
    except Exception as error:  # noqa: BLE001
        _fallo(r, reportar, "no se pudo podar el outbox de avisos", error)
    log.info(
        "vencimiento_terminado",
        extra={
            "vencidos": r.vencidos,
            "avisados": r.avisados,
            "sin_aviso": r.sin_aviso,
            "omitidos": r.omitidos,
            "fallos": r.fallos,
            "sesiones_borradas": r.sesiones_borradas,
            "reescaneadas": r.reescaneadas,
            "reescaneo_enviadas": r.reescaneo_enviadas,
            "reescaneo_reprogramadas": r.reescaneo_reprogramadas,
            "reescaneo_descartadas": r.reescaneo_descartadas,
            "outbox_podadas": r.outbox_podadas,
        },
    )
    return r


async def ejecutar_con_monitor_vencimiento(
    operacion: Callable[[], Awaitable[Resultado]],
    *,
    checkin: Callable[..., str | None] = capture_checkin,
    cron: str = CRON_POR_DEFECTO,
) -> Resultado:
    """Corre `operacion` dentro del monitor de cron de Sentry del job de vencimiento (sin DSN no hace nada)."""
    return await ejecutar_con_monitor(
        operacion,
        checkin=checkin,
        cron=cron,
        monitor_slug=MONITOR_SLUG,
        margen_minutos=_MARGEN_MINUTOS,
        duracion_maxima_minutos=_DURACION_MAXIMA_MINUTOS,
    )
