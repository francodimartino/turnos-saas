"""Punto de entrada del Render Cron Job: `python -m jobs_vencimiento` (cada 15 minutos).

Variables de entorno: `SCHEDULER_DATABASE_URL` (conexión como `scheduler_job`) y `SENTRY_DSN` (opcional).
Sale con código 1 si algo falló: así Render manda el email de fallo (§6.2).
"""

import asyncio
import logging
import os
import sys

import sentry_sdk
from observabilidad import configurar_logging, iniciar_sentry
from platform_tenancy import crear_pool

from jobs_vencimiento.proceso import ejecutar

log = logging.getLogger("jobs_vencimiento")


async def _correr(dsn: str) -> int:
    # statement_cache_size=0: compatible con el pooler de Supabase en modo transacción (§4.3).
    pool = await crear_pool(dsn, statement_cache_size=0, min_size=1, max_size=3)
    try:
        return (await ejecutar(pool)).exit_code
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
        codigo = asyncio.run(_correr(dsn))
    except Exception as error:  # noqa: BLE001 - cualquier caída del job: reportar y salir con error
        log.error("job_vencimiento_caido", extra={"motivo": type(error).__name__})
        sentry_sdk.capture_exception(error)
        codigo = 1
    sentry_sdk.flush(timeout=5)
    return codigo


if __name__ == "__main__":
    sys.exit(main())
