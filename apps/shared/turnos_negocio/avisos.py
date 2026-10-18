"""Textos de los avisos al cliente que comparten más de una app (ARCHITECTURE.md §5.4.1).

El aviso de vencimiento lo mandan dos caminos: el job de vencimiento y el bot (al liberar un pendiente vencido
sobre su horario). Los dos construyen el mensaje acá, así el texto y la plantilla no pueden divergir.
El texto lleva el turno (día, hora local y servicio) pero ningún dato del cliente.

También viven acá los textos de los demás avisos de turno (los que manda el dashboard al aprobar, rechazar,
cancelar o mover, y los de confirmación del bot): el reescaneo del outbox (`jobs_vencimiento`) los renderiza a
partir del turno, sin depender de las apps que los encolaron (`mensaje_aviso_turno`).
"""

from datetime import datetime

from turnos_negocio.zona import ZONA_HORARIA

PLANTILLA_VENCIMIENTO = "turno_vencido"

_DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


def mensaje_vencimiento(inicio: datetime, servicio: str) -> str:
    """Texto del aviso de un turno `pending` vencido. Lleva turno y servicio, nunca datos del cliente."""
    local = inicio.astimezone(ZONA_HORARIA)
    cuando = f"{_DIAS[local.weekday()]} {local.day}/{local.month} a las {local:%H:%M}"
    return (
        f"Tu solicitud de turno de {servicio} para el {cuando} venció sin respuesta del negocio y fue cancelada. "
        "Podés volver a reservar cuando quieras."
    )


# --- Textos del dashboard (aprobar, rechazar, cancelar y mover un turno) ---------------------------------------------
PLANTILLAS_DASHBOARD = {
    "turno_confirmado": "Tu turno de {servicio} del {cuando} fue confirmado.",
    "turno_rechazado": "Tu solicitud de turno de {servicio} del {cuando} fue rechazada por el negocio.",
    "turno_cancelado": "Tu turno de {servicio} del {cuando} fue cancelado por el negocio.",
    "turno_movido": "Tu turno de {servicio} fue movido para el {cuando}.",
}

# --- Textos del bot (confirmación de la reserva) ---------------------------------------------------------------------
PLANTILLA_RESERVA_CONFIRMADA = "reserva_confirmada"
PLANTILLA_RESERVA_PENDIENTE = "reserva_pendiente"
TEXTO_CONFIRMADO = "Listo, tu turno quedó confirmado."
TEXTO_PENDIENTE = (
    "Recibimos tu solicitud: tu turno queda pendiente de aprobación del negocio. "
    "Si no se aprueba antes del {limite}, se cancela solo."
)


def cuando_dashboard(inicio: datetime) -> str:
    """Fecha y hora numéricas en la zona del negocio (formato del dashboard)."""
    return inicio.astimezone(ZONA_HORARIA).strftime("%d/%m/%Y %H:%M")


def cuando_bot(momento: datetime) -> str:
    """Día, fecha y hora en la zona del negocio (formato del bot)."""
    local = momento.astimezone(ZONA_HORARIA)
    return f"{_DIAS[local.weekday()]} {local:%d/%m} a las {local:%H:%M}"


def plantillas_conocidas() -> frozenset[str]:
    return frozenset(
        {PLANTILLA_VENCIMIENTO, PLANTILLA_RESERVA_CONFIRMADA, PLANTILLA_RESERVA_PENDIENTE, *PLANTILLAS_DASHBOARD}
    )


def mensaje_aviso_turno(
    plantilla: str,
    *,
    servicio: str,
    profesional: str | None,
    inicio: datetime,
    vence: datetime | None = None,
) -> str | None:
    """Texto del aviso de `plantilla` para un turno, o `None` si la plantilla no es de turnos.

    `inicio` es el del aviso (el que se encoló), `vence` el límite de aprobación de un turno pendiente. Reproduce
    los textos que mandan el dashboard y el bot en el envío inmediato; nunca lleva datos del cliente.
    """
    if plantilla == PLANTILLA_VENCIMIENTO:
        return mensaje_vencimiento(inicio, servicio)
    if plantilla in PLANTILLAS_DASHBOARD:
        return PLANTILLAS_DASHBOARD[plantilla].format(servicio=servicio, cuando=cuando_dashboard(inicio))
    if plantilla in (PLANTILLA_RESERVA_CONFIRMADA, PLANTILLA_RESERVA_PENDIENTE):
        detalle = f"{servicio} con {profesional or 'el profesional'}, {cuando_bot(inicio)}."
        if plantilla == PLANTILLA_RESERVA_CONFIRMADA:
            return TEXTO_CONFIRMADO + "\n" + detalle
        return TEXTO_PENDIENTE.format(limite=cuando_bot(vence or inicio)) + "\n" + detalle
    return None
