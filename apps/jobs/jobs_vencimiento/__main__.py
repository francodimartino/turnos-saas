"""Punto de entrada del Render Cron Job: `python -m jobs_vencimiento` (cada 15 minutos).

Variables de entorno: `SCHEDULER_DATABASE_URL` (conexión como `scheduler_job`) `SENTRY_DSN` (opcional),
`VENCIMIENTO_CONCURRENCIA` (avisos simultáneos, 5 por defecto), `VENCIMIENTO_PLAZO_SEG`
(tope del envío de la corrida, 600 por defecto) y `VENCIMIENTO_CRON` (horario crontab UTC del monitor de cron de
Sentry, `*/15 * * * *` por defecto: igual al schedule del Cron Job; sin `SENTRY_DSN` no hay monitor).
Sale con código 1 si algo falló: así Render manda el email de fallo (§6.2).
"""

import asyncio
import logging
import os
import sys

import sentry_sdk
from observabilidad import configurar_logging, iniciar_sentry
from platform_tenancy import crear_pool

from jobs_vencimiento.proceso import (
    CONCURRENCIA_POR_DEFECTO,
    CRON_POR_DEFECTO,
    PLAZO_TOTAL_POR_DEFECTO,
    Resultado,
    ejecutar,
    ejecutar_con_monitor_vencimiento,
)

log = logging.getLogger("jobs_vencimiento")


def _numero(variable: str, defecto: float) -> float:
    try:
        valor = float(os.environ.get(variable, defecto))
    except ValueError:
        return defecto
    return valor if valor > 0 else defecto


def _cron() -> str:
    return (os.environ.get("VENCIMIENTO_CRON") or "").strip() or CRON_POR_DEFECTO


async def _correr(dsn: str) -> Resultado:
    # statement_cache_size=0: compatible con el pooler de Supabase en modo transacción (§4.3).
    pool = await crear_pool(dsn, statement_cache_size=0, servicio="jobs")
    try:
        return await ejecutar(
            pool,
            concurrencia=int(_numero("VENCIMIENTO_CONCURRENCIA", CONCURRENCIA_POR_DEFECTO)),
            plazo_total=_numero("VENCIMIENTO_PLAZO_SEG", PLAZO_TOTAL_POR_DEFECTO),
        )
    finally:
        await pool.close()


def main() -> int:
    configurar_logging()
    iniciar_sentry(os.environ.get("SENTRY_DSN"), "jobs-vencimiento")
    dsn = os.environ.get("SCHEDULER_DATABASE_URL")
    if not dsn:
        log.error("falta_configuracion", extra={"variable": "SCHEDULER_DATABASE_URL"})
        return 1
    try:
        codigo = asyncio.run(ejecutar_con_monitor_vencimiento(lambda: _correr(dsn), cron=_cron())).exit_code
    except Exception as error:  # noqa: BLE001 - cualquier caída del job: reportar y salir con error
        log.error("job_vencimiento_caido", extra={"motivo": type(error).__name__})
        sentry_sdk.capture_exception(error)
        codigo = 1
    sentry_sdk.flush(timeout=5)
    return codigo


if __name__ == "__main__":
    sys.exit(main())
