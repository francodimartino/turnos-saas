"""Límites anti-abuso opcionales del alta de turnos. Único lugar donde viven los defaults.

Cada tenant puede sobrescribirlos en `tenants.settings` (la base valida los rangos con el trigger
`tenants_validar_settings`); si la clave falta, rige el default: no se guarda en la base.
Es una regla opcional: el canal que la quiere (el bot) pasa `LimitesReserva` a `crear_turno`; el canal que no
(el staff desde el panel) no la pasa y no se aplica ninguno.
"""

import re
from dataclasses import dataclass

# (clave en tenants.settings, mínimo, máximo, default)
MAX_ACTIVOS_POR_CLIENTE = ("booking_max_active_per_customer", 1, 50, 5)
MAX_POR_HORA = ("booking_max_per_hour", 1, 60, 3)
ANTICIPACION_MINIMA_MINUTOS = ("booking_min_notice_minutes", 0, 1440, 30)

VENTANA_POR_HORA_MINUTOS = 60

MOTIVO_TOPE_ACTIVOS = "tope_activos"
MOTIVO_LIMITE_POR_HORA = "limite_por_hora"
MOTIVO_ANTICIPACION = "anticipacion"


@dataclass(frozen=True)
class LimitesReserva:
    max_activos_por_cliente: int = MAX_ACTIVOS_POR_CLIENTE[3]
    max_por_hora: int = MAX_POR_HORA[3]
    anticipacion_minutos: int = ANTICIPACION_MINIMA_MINUTOS[3]


def interpretar(valor: object, regla: tuple[str, int, int, int]) -> int:
    """Entero del setting si es válido y está en rango; si no (falta o es basura), el default."""
    _, minimo, maximo, defecto = regla
    if isinstance(valor, str) and re.fullmatch(r"\d{1,5}", valor) and minimo <= int(valor) <= maximo:
        return int(valor)
    return defecto


class ReservaRechazada(Exception):
    """Un límite impidió crear el turno. `motivo` es una de las constantes MOTIVO_*."""

    def __init__(self, motivo: str) -> None:
        super().__init__(motivo)
        self.motivo = motivo
