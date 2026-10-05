"""Job de purga diaria de tenants vencidos (ARCHITECTURE.md §2.2, §5.4.1).

Corre con el rol `purge_job`: `python -m jobs_purga`.
"""

from jobs_purga.proceso import (
    MONITOR_SLUG,
    ejecutar,
    ejecutar_con_monitor,
    exit_code,
    fabrica_calendario_desde_entorno,
    log_paso_purga,
)

__all__ = [
    "MONITOR_SLUG",
    "ejecutar",
    "ejecutar_con_monitor",
    "exit_code",
    "fabrica_calendario_desde_entorno",
    "log_paso_purga",
]
