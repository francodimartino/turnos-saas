"""Límites anti-abuso de las reservas del bot (decisión D4).

Las reglas y sus defaults viven en el dominio de turnos (`turnos_dominio.limites`), porque las aplica
`crear_turno` dentro de la transacción que inserta; este módulo los reexporta para el flujo del bot.
Solo rigen para el bot: los turnos que crea el staff desde el dashboard no están sujetos a ellos.
"""

from turnos_dominio.limites import (
    ANTICIPACION_MINIMA_MINUTOS,
    MAX_ACTIVOS_POR_CLIENTE,
    MAX_POR_HORA,
    MOTIVO_ANTICIPACION,
    MOTIVO_LIMITE_POR_HORA,
    MOTIVO_TOPE_ACTIVOS,
    VENTANA_POR_HORA_MINUTOS,
    LimitesReserva,
    ReservaRechazada,
    interpretar,
)

__all__ = [
    "ANTICIPACION_MINIMA_MINUTOS",
    "MAX_ACTIVOS_POR_CLIENTE",
    "MAX_POR_HORA",
    "MOTIVO_ANTICIPACION",
    "MOTIVO_LIMITE_POR_HORA",
    "MOTIVO_TOPE_ACTIVOS",
    "VENTANA_POR_HORA_MINUTOS",
    "LimitesReserva",
    "ReservaRechazada",
    "interpretar",
]
