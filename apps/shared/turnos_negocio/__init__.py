"""Lo propio de turnos que comparten varias apps: la zona horaria del negocio y los textos de los avisos.

`platform_notifications` es de plataforma y no sabe de turnos: por eso estos textos y la zona viven acá.
"""

from turnos_negocio.avisos import (
    PLANTILLA_AVISO_NEGOCIO_APROBACION,
    PLANTILLA_AVISO_NEGOCIO_CANCELACION,
    PLANTILLA_AVISO_NEGOCIO_MOVIMIENTO,
    PLANTILLA_AVISO_NEGOCIO_RESERVA,
    PLANTILLA_CAMBIO_HORARIO_APROBADO,
    PLANTILLA_CAMBIO_HORARIO_RECHAZADO,
    PLANTILLA_CAMBIO_HORARIO_VENCIDO,
    PLANTILLA_RESERVA_CONFIRMADA,
    PLANTILLA_RESERVA_PENDIENTE,
    PLANTILLA_VENCIMIENTO,
    PLANTILLAS_AVISO_NEGOCIO,
    PLANTILLAS_CAMBIO_HORARIO,
    PLANTILLAS_DASHBOARD,
    TEXTO_CONFIRMADO,
    TEXTO_PENDIENTE,
    mensaje_aviso_turno,
    mensaje_vencimiento,
    plantillas_cliente,
    plantillas_conocidas,
    plantillas_negocio,
)
from turnos_negocio.zona import ZONA_HORARIA

__all__ = [
    "PLANTILLA_AVISO_NEGOCIO_APROBACION",
    "PLANTILLA_AVISO_NEGOCIO_CANCELACION",
    "PLANTILLA_AVISO_NEGOCIO_MOVIMIENTO",
    "PLANTILLA_AVISO_NEGOCIO_RESERVA",
    "PLANTILLA_CAMBIO_HORARIO_APROBADO",
    "PLANTILLA_CAMBIO_HORARIO_RECHAZADO",
    "PLANTILLA_CAMBIO_HORARIO_VENCIDO",
    "PLANTILLAS_AVISO_NEGOCIO",
    "PLANTILLAS_CAMBIO_HORARIO",
    "PLANTILLAS_DASHBOARD",
    "PLANTILLA_RESERVA_CONFIRMADA",
    "PLANTILLA_RESERVA_PENDIENTE",
    "PLANTILLA_VENCIMIENTO",
    "TEXTO_CONFIRMADO",
    "TEXTO_PENDIENTE",
    "ZONA_HORARIA",
    "mensaje_aviso_turno",
    "mensaje_vencimiento",
    "plantillas_cliente",
    "plantillas_conocidas",
    "plantillas_negocio",
]
