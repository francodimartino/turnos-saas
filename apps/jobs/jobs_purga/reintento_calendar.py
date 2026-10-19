"""Reintento diario de las limpiezas de Google Calendar que fallaron (ARCHITECTURE.md §5.4.1, §7.1).

Cuando la anonimización o la purga no logran limpiar un evento queda `calendar_scrub_failed` en `audit_log`. Este
paso del job de purga vuelve a intentarlo cada día con las funciones de base `list_calendar_scrubs_pending` y
`record_calendar_scrub_result` (solo `purge_job`):

1. una transacción corta lista las pendientes (las más viejas primero, hasta `limite` por corrida);
2. por cada una, sin ninguna conexión tomada mientras se espera a Google (el servicio de Calendar lee la
   credencial con `get_integration_secret` en una transacción corta del tenant, `ConexionBajoDemanda`), se limpia
   el evento con un tope de tiempo por evento y otro para toda la corrida;
3. otra transacción corta registra el resultado.

Un fallo de una limpieza no corta las demás. Sin integración (credenciales ya borradas) no hay nada que probar
con Google, pero sí se registra un reintento fallido con motivo `sin_integracion` en cada corrida: suma intentos y
llega al umbral como cualquier otra. Superado el umbral se alerta con un mensaje aparte que pide descartarla a
mano (`discard_calendar_scrub`), y NO hace fallar el job: el reintento diario no puede arreglarla. Sin Google
configurado tampoco se registra un intento (no se intentó nada): se anota la causa y se sigue.

Alerta: una limpieza que sigue sin resolverse con `umbral_intentos` o más intentos, o `umbral_dias` o más días
desde su primer fallo, es persistente. Las persistentes (con integración) se reportan a Sentry en UN solo mensaje
agregado, con etiquetas numéricas y códigos de motivo (sin ids de evento, de cliente ni PII),
y el job termina con código 1.
Valores por defecto: 5 intentos (el primer fallo cuenta como el 1 y hay un reintento por día: unos 4 días de
reintentos) o 7 días; se cambian con `CALENDAR_SCRUB_UMBRAL_INTENTOS` y `CALENDAR_SCRUB_UMBRAL_DIAS`. También se
configuran `CALENDAR_SCRUB_LIMITE` (pendientes por corrida, 200, máximo 1000), `CALENDAR_SCRUB_TIMEOUT_SEG` (por
evento, 30) y `CALENDAR_SCRUB_PLAZO_SEG` (toda la pasada, 600).
"""

import asyncio
import logging
import os
import time
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from platform_admin import ReportadorErrores
from platform_tenancy import tenant_tx

log = logging.getLogger("jobs_purga")

MOTIVO_CALENDAR_NO_CONFIGURADO = "calendar_no_configurado"
MOTIVO_TIMEOUT = "timeout"
MOTIVO_INESPERADO = "error_inesperado"
MOTIVO_SIN_INTEGRACION = "sin_integracion"

UMBRAL_INTENTOS_POR_DEFECTO = 5
UMBRAL_DIAS_POR_DEFECTO = 7
LIMITE_POR_DEFECTO = 200  # pendientes por corrida (la función de base acepta 1..1000)
TIMEOUT_EVENTO_SEG = 30.0
PLAZO_TOTAL_SEG = 600.0  # menor que el max_runtime del monitor de cron (30 min), con margen para la purga

_SQL_LISTAR = "select * from public.list_calendar_scrubs_pending($1)"
_SQL_REGISTRAR = "select public.record_calendar_scrub_result($1, $2, $3, $4)"
_SQLSTATE_NO_DATA_FOUND = "P0002"


class ConexionBajoDemanda:
    """Conexión con la interfaz mínima de asyncpg que abre una transacción corta del tenant por cada consulta.

    El servicio de Calendar usa la base solo para leer la credencial; así ninguna conexión del pool queda tomada
    mientras se espera a Google.
    """

    def __init__(self, pool: Any, tenant_id: uuid.UUID) -> None:
        self._pool = pool
        self._tenant_id = tenant_id

    async def fetchval(self, sql: str, *args: Any) -> Any:
        async with tenant_tx(self._pool, self._tenant_id) as conn:
            return await conn.fetchval(sql, *args)

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        async with tenant_tx(self._pool, self._tenant_id) as conn:
            return await conn.fetchrow(sql, *args)

    async def fetch(self, sql: str, *args: Any) -> Any:
        async with tenant_tx(self._pool, self._tenant_id) as conn:
            return await conn.fetch(sql, *args)

    async def execute(self, sql: str, *args: Any) -> Any:
        async with tenant_tx(self._pool, self._tenant_id) as conn:
            return await conn.execute(sql, *args)


@dataclass
class ResultadoLimpiezas:
    pendientes: int = 0
    resueltas: int = 0
    reintentos_fallidos: int = 0
    sin_integracion: int = 0
    sin_configurar: int = 0
    omitidas: int = 0  # no intentadas porque se agotó el plazo de la corrida
    errores: int = 0  # fallos del propio paso (listar o registrar), no de Google
    persistentes: int = 0  # con integración: hacen fallar el job
    sin_integracion_persistentes: int = 0  # sin integración: se alertan para descartarlas a mano, no fallan el job

    @property
    def ok(self) -> bool:
        return self.errores == 0 and self.persistentes == 0


@dataclass(frozen=True)
class ConfigReintento:
    limite: int = LIMITE_POR_DEFECTO
    umbral_intentos: int = UMBRAL_INTENTOS_POR_DEFECTO
    umbral_dias: int = UMBRAL_DIAS_POR_DEFECTO
    timeout_evento: float = TIMEOUT_EVENTO_SEG
    plazo_total: float = PLAZO_TOTAL_SEG

    @classmethod
    def desde_entorno(cls, entorno: Any = None) -> "ConfigReintento":
        """Lee `CALENDAR_SCRUB_*` del entorno; un valor ausente o inválido deja el default (nunca tira el job)."""
        entorno = os.environ if entorno is None else entorno

        def numero(nombre: str, tipo: type, defecto: Any) -> Any:
            try:
                valor = tipo(entorno.get(nombre) or defecto)
            except ValueError:
                return defecto
            return valor if valor > 0 else defecto

        return cls(
            limite=min(numero("CALENDAR_SCRUB_LIMITE", int, LIMITE_POR_DEFECTO), 1000),
            umbral_intentos=numero("CALENDAR_SCRUB_UMBRAL_INTENTOS", int, UMBRAL_INTENTOS_POR_DEFECTO),
            umbral_dias=numero("CALENDAR_SCRUB_UMBRAL_DIAS", int, UMBRAL_DIAS_POR_DEFECTO),
            timeout_evento=numero("CALENDAR_SCRUB_TIMEOUT_SEG", float, TIMEOUT_EVENTO_SEG),
            plazo_total=numero("CALENDAR_SCRUB_PLAZO_SEG", float, PLAZO_TOTAL_SEG),
        )


async def _limpiar(pool: Any, fabrica: Callable[[Any], Any], fila: Any, timeout: float) -> str | None:  # noqa: ASYNC109 - se pasa tal cual a asyncio.wait_for; el plazo es por evento
    """Intenta limpiar el evento. Devuelve None si quedó limpio o el código de motivo del fallo (sin PII)."""
    try:
        servicio = fabrica(ConexionBajoDemanda(pool, fila["tenant_id"]))
        pares = [(fila["integration_id"], fila["event_id"])]
        fallidos = await asyncio.wait_for(servicio.limpiar_eventos(pares), timeout)
    except (asyncio.TimeoutError, TimeoutError):  # noqa: UP041 - en 3.10 son clases distintas
        return MOTIVO_TIMEOUT
    except Exception as error:  # noqa: BLE001 - un fallo no corta el resto
        log.error("calendar_reintento_inesperado", extra={"motivo": type(error).__name__})
        return MOTIVO_INESPERADO
    return fallidos[0].motivo if fallidos else None


async def _registrar(pool: Any, fila: Any, resuelta: bool, motivo: str | None) -> bool:
    """Registra el resultado. False si la función de base dijo que ya no estaba pendiente (otro job la resolvió)."""
    try:
        async with pool.acquire() as conn:
            await conn.fetchval(_SQL_REGISTRAR, fila["tenant_id"], fila["event_id"], resuelta, motivo)
    except Exception as error:  # noqa: BLE001
        if getattr(error, "sqlstate", None) == _SQLSTATE_NO_DATA_FOUND:
            return False
        raise
    return True


def _dias(primer_fallo: datetime, ahora: datetime) -> int:
    if primer_fallo.tzinfo is None:
        primer_fallo = primer_fallo.replace(tzinfo=timezone.utc)  # noqa: UP017
    return (ahora - primer_fallo).days


def _es_persistente(intentos: int, primer_fallo: datetime, ahora: datetime, config: ConfigReintento) -> bool:
    return intentos >= config.umbral_intentos or _dias(primer_fallo, ahora) >= config.umbral_dias


async def reintentar_limpiezas(
    pool: Any,
    fabrica_calendario: Callable[[Any], Any],
    reportador: ReportadorErrores,
    config: ConfigReintento | None = None,
    *,
    ahora: Callable[[], datetime] = lambda: datetime.now(timezone.utc),  # noqa: UP017
    reloj: Callable[[], float] = time.monotonic,
) -> ResultadoLimpiezas:
    """Una pasada de reintentos. Nunca lanza: lo que falla se cuenta en el resultado y se reporta."""
    config = config or ConfigReintento()
    r = ResultadoLimpiezas()
    try:
        async with pool.acquire() as conn:
            filas = await conn.fetch(_SQL_LISTAR, config.limite)
    except Exception as error:  # noqa: BLE001
        r.errores += 1
        reportador.capturar_excepcion(error, paso="calendar_scrub_retry_listado")
        log.error("calendar_reintento_listado_fallido", extra={"motivo": type(error).__name__})
        return r
    r.pendientes = len(filas)
    limite_tiempo = reloj() + config.plazo_total
    sin_resolver: list[tuple[int, datetime, str]] = []  # (intentos, primer fallo, motivo)
    sin_integracion: list[tuple[int, datetime, str]] = []  # idem, de las que no tienen integración

    for fila in filas:
        intentos, primero = fila["attempts"], fila["first_failed_at"]
        if fila["integration_id"] is None:
            r.sin_integracion += 1
            log.warning(
                "calendar_reintento_sin_integracion",
                extra={"tenant_id": str(fila["tenant_id"]), "motivo": MOTIVO_SIN_INTEGRACION},
            )
            try:
                registrada = await _registrar(pool, fila, False, MOTIVO_SIN_INTEGRACION)
            except Exception as error:  # noqa: BLE001
                r.errores += 1
                reportador.capturar_excepcion(
                    error, paso="calendar_scrub_retry_registro", tenant_id=str(fila["tenant_id"])
                )
                log.error("calendar_reintento_registro_fallido", extra={"motivo": type(error).__name__})
                continue
            sin_integracion.append((intentos + int(registrada), primero, MOTIVO_SIN_INTEGRACION))
            continue
        if reloj() >= limite_tiempo:
            r.omitidas += 1
            sin_resolver.append((intentos, primero, fila["last_motivo"] or "desconocido"))
            continue
        motivo = await _limpiar(pool, fabrica_calendario, fila, config.timeout_evento)
        if motivo == MOTIVO_CALENDAR_NO_CONFIGURADO:
            r.sin_configurar += 1  # no se intentó nada: no suma un intento
            sin_resolver.append((intentos, primero, motivo))
            continue
        try:
            registrada = await _registrar(pool, fila, motivo is None, motivo)
        except Exception as error:  # noqa: BLE001
            r.errores += 1
            reportador.capturar_excepcion(error, paso="calendar_scrub_retry_registro", tenant_id=str(fila["tenant_id"]))
            log.error("calendar_reintento_registro_fallido", extra={"motivo": type(error).__name__})
            sin_resolver.append((intentos, primero, motivo or "registro_fallido"))
            continue
        if motivo is None:
            r.resueltas += int(registrada)
        else:
            r.reintentos_fallidos += 1
            sin_resolver.append((intentos + int(registrada), primero, motivo))

    if r.sin_configurar:
        log.warning("calendar_reintento_sin_configurar", extra={"motivo": MOTIVO_CALENDAR_NO_CONFIGURADO})
    ahora_utc = ahora()
    persistentes = [s for s in sin_resolver if _es_persistente(s[0], s[1], ahora_utc, config)]
    r.persistentes = len(persistentes)
    if persistentes:
        _alertar(reportador, persistentes, ahora_utc, config)
    huerfanas = [s for s in sin_integracion if _es_persistente(s[0], s[1], ahora_utc, config)]
    r.sin_integracion_persistentes = len(huerfanas)
    if huerfanas:
        _alertar_sin_integracion(reportador, huerfanas, ahora_utc, config)
    log.info(
        "calendar_reintentos_terminados",
        extra={
            "pendientes": r.pendientes,
            "resueltas": r.resueltas,
            "fallidos": r.reintentos_fallidos,
            "sin_integracion": r.sin_integracion,
            "sin_configurar": r.sin_configurar,
            "omitidas": r.omitidas,
            "persistentes": r.persistentes,
        },
    )
    return r


def _alertar(
    reportador: ReportadorErrores,
    persistentes: list[tuple[int, datetime, str]],
    ahora: datetime,
    config: ConfigReintento,
) -> None:
    """Un solo mensaje agregado a Sentry: cantidades y códigos de motivo, nunca ids de evento ni PII."""
    motivos = Counter(m for _, _, m in persistentes)
    reportador.capturar_mensaje(
        "limpiezas de Google Calendar que siguen sin resolverse",
        paso="calendar_scrub_retry",
        persistentes=len(persistentes),
        max_intentos=max(i for i, _, _ in persistentes),
        max_dias=max(_dias(p, ahora) for _, p, _ in persistentes),
        umbral_intentos=config.umbral_intentos,
        umbral_dias=config.umbral_dias,
        motivos=",".join(f"{m}:{n}" for m, n in sorted(motivos.items())),
    )


def _alertar_sin_integracion(
    reportador: ReportadorErrores,
    huerfanas: list[tuple[int, datetime, str]],
    ahora: datetime,
    config: ConfigReintento,
) -> None:
    """Mensaje agregado aparte: estas limpiezas no se arreglan reintentando, hay que descartarlas a mano."""
    reportador.capturar_mensaje(
        "limpiezas de Google Calendar sin integracion que superaron el umbral: descartarlas manualmente "
        "con discard_calendar_scrub",
        paso="calendar_scrub_sin_integracion",
        sin_integracion=len(huerfanas),
        max_intentos=max(i for i, _, _ in huerfanas),
        max_dias=max(_dias(p, ahora) for _, p, _ in huerfanas),
        umbral_intentos=config.umbral_intentos,
        umbral_dias=config.umbral_dias,
    )
