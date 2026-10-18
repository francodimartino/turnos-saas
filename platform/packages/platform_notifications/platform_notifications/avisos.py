"""Textos de los avisos al cliente que comparten más de una app (ARCHITECTURE.md §5.4.1).

El aviso de vencimiento lo mandan dos caminos: el job de vencimiento y el bot (al liberar un pendiente vencido
sobre su horario). Los dos construyen el mensaje acá, así el texto y la plantilla no pueden divergir.
El texto lleva el turno (día, hora local y servicio) pero ningún dato del cliente.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

PLANTILLA_VENCIMIENTO = "turno_vencido"

# Zona del negocio (§1.4). Único punto donde este módulo la define; la hora del aviso se formatea con ella.
ZONA_NEGOCIO = ZoneInfo("America/Argentina/Buenos_Aires")

_DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


def mensaje_vencimiento(inicio: datetime, servicio: str) -> str:
    """Texto del aviso de un turno `pending` vencido. Lleva turno y servicio, nunca datos del cliente."""
    local = inicio.astimezone(ZONA_NEGOCIO)
    cuando = f"{_DIAS[local.weekday()]} {local.day}/{local.month} a las {local:%H:%M}"
    return (
        f"Tu solicitud de turno de {servicio} para el {cuando} venció sin respuesta del negocio y fue cancelada. "
        "Podés volver a reservar cuando quieras."
    )
