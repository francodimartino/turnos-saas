"""Monitor de cron de Sentry compartido por los Cron Jobs (ARCHITECTURE.md §6.1, §6.2).

Cada job aporta su slug, su horario, su margen y su duración máxima; este módulo solo arma el check-in de inicio
(`in_progress`) y el de fin (`ok`/`error`). Sin `SENTRY_DSN`, `capture_checkin` no hace nada.
"""

import time
from collections.abc import Awaitable, Callable
from typing import Any

from sentry_sdk.crons import capture_checkin

Checkin = Callable[..., str | None]


async def ejecutar_con_monitor(
    operacion: Callable[[], Awaitable[Any]],
    *,
    monitor_slug: str,
    cron: str,
    margen_minutos: int,
    duracion_maxima_minutos: int,
    checkin: Checkin = capture_checkin,
    reloj: Callable[[], float] = time.monotonic,
) -> Any:
    """Corre `operacion` entre el check-in de inicio y el de fin del monitor de cron de Sentry.

    El de fin es `error` si la corrida no fue limpia o si la operación lanzó (la excepción sigue subiendo).
    La operación devuelve algo con `.ok`.
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
