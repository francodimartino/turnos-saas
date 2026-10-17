"""Punto de entrada del Render Cron Job de purga diaria: `python -m jobs_purga` (§5.4.1).

Variables de entorno:
- `PURGE_DATABASE_URL`: conexión como `purge_job` (sin grants sobre tablas).
- `SUPABASE_URL` y `SUPABASE_SERVICE_ROLE_KEY`: `service_role` solo para eliminar cuentas por la Auth Admin API.
- `SENTRY_DSN` (opcional) y `PURGA_CRON` (opcional, horario crontab UTC del monitor; por defecto el de `proceso`).
- `GOOGLE_OAUTH_*` (opcionales): sin ellas el paso 0 registra
  `calendar_scrub_failed` por configuración y la purga sigue.

Sale con código 1 si algo falló: así Render manda el email de fallo (§6.2).
"""

import asyncio
import logging
import os
import sys

import httpx
import sentry_sdk
from observabilidad import configurar_logging, iniciar_sentry
from platform_admin import ClienteAuthAdminHttp, ReportadorSentry, ResultadoPurga
from platform_tenancy import crear_pool

from jobs_purga.proceso import (
    CRON_POR_DEFECTO,
    ejecutar,
    ejecutar_con_monitor,
    exit_code,
    fabrica_calendario_desde_entorno,
)

log = logging.getLogger("jobs_purga")


async def _correr() -> ResultadoPurga:
    dsn = os.environ.get("PURGE_DATABASE_URL")
    if not dsn:
        raise ValueError("Falta la variable PURGE_DATABASE_URL")
    cliente_auth = ClienteAuthAdminHttp.desde_entorno(os.environ)
    # statement_cache_size=0: compatible con el pooler de Supabase en modo transacción (§4.3).
    pool = await crear_pool(dsn, statement_cache_size=0, servicio="jobs")
    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            return await ejecutar(pool, cliente_auth, fabrica_calendario_desde_entorno(http), ReportadorSentry())
    finally:
        await pool.close()


async def _con_monitor() -> ResultadoPurga:
    return await ejecutar_con_monitor(_correr, cron=os.environ.get("PURGA_CRON") or CRON_POR_DEFECTO)


def main() -> int:
    configurar_logging()
    iniciar_sentry(os.environ.get("SENTRY_DSN"), "jobs-purga")
    try:
        resultado = asyncio.run(_con_monitor())
        codigo = exit_code(resultado)
        log.info(
            "purga_terminada",
            extra={"purgados": resultado.purgados, "fallidos": resultado.fallidos, "codigo": codigo},
        )
    except Exception as error:  # noqa: BLE001 - cualquier caída del job: reportar y salir con error
        log.error("job_purga_caido", extra={"motivo": type(error).__name__})
        sentry_sdk.capture_exception(error)
        codigo = 1
    sentry_sdk.flush(timeout=5)
    return codigo


if __name__ == "__main__":
    sys.exit(main())
