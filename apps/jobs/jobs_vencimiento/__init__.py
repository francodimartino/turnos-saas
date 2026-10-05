"""Job de vencimiento de turnos y sesiones, cada 15 minutos (ARCHITECTURE.md §5.4.1).

Corre con el rol `scheduler_job`: `python -m jobs_vencimiento`.
"""

from jobs_vencimiento.proceso import MENSAJE_VENCIMIENTO, Resultado, ejecutar, reportar_a_sentry

__all__ = ["MENSAJE_VENCIMIENTO", "Resultado", "ejecutar", "reportar_a_sentry"]
