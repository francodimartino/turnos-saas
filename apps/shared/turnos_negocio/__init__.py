"""Lo propio de turnos que comparten varias apps: la zona horaria del negocio y los textos de los avisos.

`platform_notifications` es de plataforma y no sabe de turnos: por eso estos textos y la zona viven acá.
"""

from turnos_negocio.avisos import (
    PLANTILLA_RESERVA_CONFIRMADA,
    PLANTILLA_RESERVA_PENDIENTE,
    PLANTILLA_VENCIMIENTO,
    PLANTILLAS_DASHBOARD,
    TEXTO_CONFIRMADO,
    TEXTO_PENDIENTE,
    mensaje_aviso_turno,
    mensaje_vencimiento,
    plantillas_conocidas,
)
from turnos_negocio.zona import ZONA_HORARIA

__all__ = [
    "PLANTILLAS_DASHBOARD",
    "PLANTILLA_RESERVA_CONFIRMADA",
    "PLANTILLA_RESERVA_PENDIENTE",
    "PLANTILLA_VENCIMIENTO",
    "TEXTO_CONFIRMADO",
    "TEXTO_PENDIENTE",
    "ZONA_HORARIA",
    "mensaje_aviso_turno",
    "mensaje_vencimiento",
    "plantillas_conocidas",
]
