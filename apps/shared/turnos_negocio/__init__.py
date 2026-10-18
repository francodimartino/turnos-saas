"""Lo propio de turnos que comparten varias apps: la zona horaria del negocio y el texto del aviso de vencimiento.

`platform_notifications` es de plataforma y no sabe de turnos: por eso estos textos y la zona viven acá.
"""

from turnos_negocio.avisos import PLANTILLA_VENCIMIENTO, mensaje_vencimiento
from turnos_negocio.zona import ZONA_HORARIA

__all__ = ["PLANTILLA_VENCIMIENTO", "ZONA_HORARIA", "mensaje_vencimiento"]
