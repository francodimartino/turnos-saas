"""Orquestación del Cron Job de purga diaria (ARCHITECTURE.md §5.4.1, §6.1, §6.2).

La cascada vive en `platform_admin.purgar_tenants_vencidos`; acá solo se arma lo que necesita el job:

- el servicio de Google Calendar para el paso 0 (si faltan las variables OAuth del proyecto Google, el paso 0 no
  puede limpiar nada: cada evento queda registrado como `calendar_scrub_failed` con motivo de configuración y la
  purga sigue, en vez de caerse el job entero);
- el log estructurado `tenant_purge_step` (evento obligatorio, sin PII);
- el monitor de cron de Sentry: check-in `in_progress` al iniciar y `ok`/`error` al terminar;
- el código de salida: 0 solo si la corrida fue limpia, para que Render avise por email cuando falla (§6.2).
"""

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from observabilidad import log_evento_obligatorio
from platform_admin import ReportadorErrores, ResultadoPurga, purgar_tenants_vencidos
from platform_admin.auth_admin import ClienteAuthEliminacion
from platform_calendar import (
    ConfigGoogle,
    EventoNoLimpiado,
    crear_calendario,
)
from platform_secrets import crear_almacen
from sentry_sdk.crons import capture_checkin

from jobs_purga.anclaje import anclar_cadenas
from jobs_purga.reintento_calendar import ConfigReintento, ResultadoLimpiezas, reintentar_limpiezas

log = logging.getLogger("jobs_purga")

MONITOR_SLUG = "purga-diaria-tenants"
MOTIVO_CALENDAR_SIN_CONFIGURAR = "calendar_no_configurado"

# Horario por defecto del monitor (UTC, el huso de Render). Tiene que coincidir con el schedule del Cron Job.
CRON_POR_DEFECTO = "0 6 * * *"
_MARGEN_MINUTOS = 60
_DURACION_MAXIMA_MINUTOS = 30

Checkin = Callable[..., str | None]


@dataclass(frozen=True)
class ResultadoJob:
    """La purga de tenants más el reintento de limpiezas de Calendar: el job es limpio si ambos lo fueron."""

    purga: ResultadoPurga
    limpiezas: ResultadoLimpiezas
    ancla_ok: bool = True  # False si no se pudieron leer las cabezas de la cadena de auditoría para anclarlas

    @property
    def ok(self) -> bool:
        return self.purga.ok and self.limpiezas.ok and self.ancla_ok

    @property
    def purgados(self) -> int:
        return self.purga.purgados

    @property
    def fallidos(self) -> int:
        return self.purga.fallidos


def exit_code(resultado: ResultadoPurga | ResultadoJob) -> int:
    """0 si la corrida fue limpia (ningún tenant, la poda ni las limpiezas persistentes fallaron); 1 si no, para
    que Render avise."""
    return 0 if resultado.ok else 1


def log_paso_purga(evento: str, **campos: Any) -> None:
    """`log_paso` de la cascada: el evento obligatorio `tenant_purge_step` (§6.3), que rechaza campos de PII."""
    log_evento_obligatorio(evento, **campos)


class _CalendarioSinConfigurar:
    """Paso 0 sin credenciales del proyecto Google: no puede limpiar nada, y lo dice evento por evento."""

    async def limpiar_eventos(self, pares: list[tuple[uuid.UUID, str]]) -> list[EventoNoLimpiado]:
        return [EventoNoLimpiado(i, e, MOTIVO_CALENDAR_SIN_CONFIGURAR) for i, e in pares]


def config_google_desde_entorno() -> ConfigGoogle | None:
    """Config de Google del job, decidida UNA vez al arrancar: `None` = Google sin configurar.

    Con la configuración a medias o insegura lanza `ErrorConfiguracionCalendar` (el job no arranca) con un mensaje
    que nombra la variable.
    """
    return ConfigGoogle.opcional_desde_entorno(requiere_state=False)


def fabrica_calendario(config: ConfigGoogle | None, http: Any) -> Callable[[Any], Any]:
    """Fábrica de servicio de calendario por conexión (la que pide `purgar_tenants_vencidos`).

    Con la configuración de Google arma el servicio real sobre el Vault de esa conexión; sin ella (`None`),
    devuelve uno que marca todos los eventos como no limpiados por configuración (el job sigue y la purga no se
    frena). No lee el entorno: la config llega ya decidida.
    """
    if config is None:
        log.warning("calendar_sin_configurar")
        return lambda conn: _CalendarioSinConfigurar()
    return lambda conn: crear_calendario(config, crear_almacen(conn), http)


def fabrica_calendario_desde_entorno(http: Any) -> Callable[[Any], Any]:
    """Lee la config de Google del entorno (una vez) y arma con ella la fábrica de calendario."""
    return fabrica_calendario(config_google_desde_entorno(), http)


async def ejecutar_con_monitor(
    operacion: Callable[[], Awaitable[Any]],
    *,
    checkin: Checkin = capture_checkin,
    reloj: Callable[[], float] = time.monotonic,
    cron: str = CRON_POR_DEFECTO,
    monitor_slug: str = MONITOR_SLUG,
    margen_minutos: int = _MARGEN_MINUTOS,
    duracion_maxima_minutos: int = _DURACION_MAXIMA_MINUTOS,
) -> Any:
    """Corre `operacion` entre el check-in de inicio y el de fin del monitor de cron de Sentry.

    El de fin es `error` si la corrida no fue limpia o si la operación lanzó (la excepción sigue subiendo).
    Sin DSN, `capture_checkin` no hace nada. El slug, el margen y la duración máxima tienen por defecto los de la
    purga; otros jobs (vencimiento) pasan los suyos. La operación devuelve algo con `.ok`.
    """
    config = {
        "schedule": {"type": "crontab", "value": cron},
        "timezone": "UTC",
        "checkin_margin": margen_minutos,
        "max_runtime": duracion_maxima_minutos,
    }
    id_checkin = checkin(monitor_slug=monitor_slug, status="in_progress", monitor_config=config)
    inicio = reloj()
    estado = "error"
    try:
        resultado = await operacion()
        estado = "ok" if resultado.ok else "error"
        return resultado
    finally:
        checkin(monitor_slug=monitor_slug, check_in_id=id_checkin, status=estado, duration=reloj() - inicio)


async def ejecutar(
    pool: Any,
    cliente_auth: ClienteAuthEliminacion,
    fabrica_calendario: Callable[[Any], Any],
    reportador: ReportadorErrores,
) -> ResultadoPurga:
    """Una corrida de la purga con el pool de `purge_job`, el log obligatorio y el reportador inyectados."""
    return await purgar_tenants_vencidos(
        pool, cliente_auth, fabrica_calendario, reportador=reportador, log_paso=log_paso_purga
    )


async def ejecutar_job(
    pool: Any,
    cliente_auth: ClienteAuthEliminacion,
    fabrica_calendario: Callable[[Any], Any],
    reportador: ReportadorErrores,
    config_reintento: ConfigReintento | None = None,
) -> ResultadoJob:
    """La corrida completa del Cron Job: purga de tenants vencidos y, después, reintento de las limpiezas de
    Calendar pendientes (la purga primero: lo que ella no logre limpiar queda listado para el reintento de mañana)
    y, al final, el anclaje de las cabezas de la cadena de auditoría (si falla, la purga y los reintentos ya se
    hicieron)."""
    purga = await ejecutar(pool, cliente_auth, fabrica_calendario, reportador)
    limpiezas = await reintentar_limpiezas(pool, fabrica_calendario, reportador, config_reintento)
    ancla_ok = await anclar_cadenas(pool, reportador)
    return ResultadoJob(purga, limpiezas, ancla_ok)
