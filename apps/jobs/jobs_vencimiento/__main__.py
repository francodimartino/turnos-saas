"""Punto de entrada del Render Cron Job: `python -m jobs_vencimiento` (cada 15 minutos).

Variables de entorno: `SCHEDULER_DATABASE_URL` (conexión como `scheduler_job`) `SENTRY_DSN` (opcional),
`VENCIMIENTO_CONCURRENCIA` (avisos simultáneos, 5 por defecto), `VENCIMIENTO_PLAZO_SEG`
(tope del envío de la corrida, 600 por defecto) y `VENCIMIENTO_CRON` (horario crontab UTC del monitor de cron de
Sentry, `*/15 * * * *` por defecto: igual al schedule del Cron Job; sin `SENTRY_DSN` no hay monitor).
Reescaneo del outbox de avisos (§6.4): `OUTBOX_LOTE` (filas por corrida, 50), `OUTBOX_ARRIENDO_SEG` (segundos que una
fila reclamada queda reservada, 900: mayor que el plazo de la corrida), `OUTBOX_MAX_INTENTOS` (reclamos antes de
dejarla `failed`, 5) y `OUTBOX_RETENCION_DIAS` (borrado de filas ya cerradas, 30).
`TELEGRAM_API_BASE_URL` (opcional, solo para un simulador local; se valida al arrancar).
Sale con código 1 si algo falló: así Render manda el email de fallo (§6.2).
"""

import asyncio
import logging
import os
import sys

import sentry_sdk
from platform_notifications import base_url_api
from platform_tenancy import crear_pool
from turnos_observabilidad import configurar_logging, iniciar_sentry

from jobs_vencimiento.proceso import (
    ARRIENDO_OUTBOX_POR_DEFECTO,
    CONCURRENCIA_POR_DEFECTO,
    CRON_POR_DEFECTO,
    LOTE_OUTBOX_POR_DEFECTO,
    MAX_INTENTOS_OUTBOX_POR_DEFECTO,
    PLAZO_TOTAL_POR_DEFECTO,
    RETENCION_OUTBOX_DIAS_POR_DEFECTO,
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


def _entero(variable: str, defecto: int, minimo: int, maximo: int) -> int:
    """Entero de la variable de entorno; si falta, no es un número o está fuera de rango, el valor por defecto."""
    try:
        valor = int(os.environ.get(variable, defecto))
    except ValueError:
        return defecto
    return valor if minimo <= valor <= maximo else defecto


def _cron() -> str:
    return (os.environ.get("VENCIMIENTO_CRON") or "").strip() or CRON_POR_DEFECTO


async def _correr(dsn: str, base_url_telegram: str) -> Resultado:
    # statement_cache_size=0: compatible con el pooler de Supabase en modo transacción (§4.3).
    pool = await crear_pool(dsn, statement_cache_size=0, servicio="jobs")
    try:
        return await ejecutar(
            pool,
            concurrencia=int(_numero("VENCIMIENTO_CONCURRENCIA", CONCURRENCIA_POR_DEFECTO)),
            plazo_total=_numero("VENCIMIENTO_PLAZO_SEG", PLAZO_TOTAL_POR_DEFECTO),
            # Los límites de las funciones del outbox (lote 1..500, arriendo 30..86400, intentos 2..20, retención
            # 1..365) se acotan acá: un valor fuera de rango haría fallar el reclamo en cada corrida.
            lote_outbox=_entero("OUTBOX_LOTE", LOTE_OUTBOX_POR_DEFECTO, 1, 500),
            arriendo_outbox=_entero("OUTBOX_ARRIENDO_SEG", ARRIENDO_OUTBOX_POR_DEFECTO, 30, 86400),
            max_intentos_outbox=_entero("OUTBOX_MAX_INTENTOS", MAX_INTENTOS_OUTBOX_POR_DEFECTO, 2, 20),
            retencion_outbox_dias=_entero("OUTBOX_RETENCION_DIAS", RETENCION_OUTBOX_DIAS_POR_DEFECTO, 1, 365),
            base_url_telegram=base_url_telegram,
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
        # La base de la Bot API se valida una vez al arrancar: un valor inseguro impide correr.
        base_url_telegram = base_url_api(os.environ)
    except ValueError as error:
        log.error("falta_configuracion", extra={"motivo": str(error)})
        return 1
    try:
        codigo = asyncio.run(
            ejecutar_con_monitor_vencimiento(lambda: _correr(dsn, base_url_telegram), cron=_cron())
        ).exit_code
    except Exception as error:  # noqa: BLE001 - cualquier caída del job: reportar y salir con error
        log.error("job_vencimiento_caido", extra={"motivo": type(error).__name__})
        sentry_sdk.capture_exception(error)
        codigo = 1
    sentry_sdk.flush(timeout=5)
    return codigo


if __name__ == "__main__":
    sys.exit(main())
