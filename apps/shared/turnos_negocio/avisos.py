"""Textos de los avisos al cliente que comparten más de una app (ARCHITECTURE.md §5.4.1).

El aviso de vencimiento lo mandan dos caminos: el job de vencimiento y el bot (al liberar un pendiente vencido
sobre su horario). Los dos construyen el mensaje acá, así el texto y la plantilla no pueden divergir.
El texto lleva el turno (día, hora local y servicio) pero ningún dato del cliente.

También viven acá los textos de los demás avisos de turno (los que manda el dashboard al aprobar, rechazar,
cancelar o mover, y los de confirmación del bot): el servicio enviador (`enviador_app`, todos los canales) y el
reescaneo del outbox (`jobs_vencimiento`, solo para fichas con un único Telegram verificado) los renderizan a partir
del turno, sin depender de las apps que los encolaron (`mensaje_aviso_turno`). El asunto del email de cada plantilla
sale de `asunto_aviso_turno`.

Los avisos del cambio de horario al cliente (aprobado, rechazado, vencido) y los avisos al negocio (reserva,
cancelación, movimiento y pedido de aprobación) se arman acá también, con los nombres de plantilla que admite el
CHECK de `notification_outbox`. Ninguno lleva datos del cliente: solo servicio, profesional y horario.
"""

import json
from datetime import datetime
from typing import Any

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

# --- Textos del cambio de horario (al cliente) ---------------------------------------------------------------------
# Con `aprobado` el horario del aviso es el nuevo; con `rechazado` y `vencido`, el que el turno conserva.
PLANTILLA_CAMBIO_HORARIO_APROBADO = "cambio_horario_aprobado"
PLANTILLA_CAMBIO_HORARIO_RECHAZADO = "cambio_horario_rechazado"
PLANTILLA_CAMBIO_HORARIO_VENCIDO = "cambio_horario_vencido"
PLANTILLAS_CAMBIO_HORARIO = {
    PLANTILLA_CAMBIO_HORARIO_APROBADO: (
        "Tu cambio de horario de {servicio} fue aprobado: tu turno quedó para el {cuando}."
    ),
    PLANTILLA_CAMBIO_HORARIO_RECHAZADO: (
        "El negocio no aprobó el cambio de horario de tu turno de {servicio}: sigue para el {cuando}."
    ),
    PLANTILLA_CAMBIO_HORARIO_VENCIDO: (
        "Tu pedido de cambio de horario de {servicio} venció sin respuesta del negocio: "
        "tu turno sigue para el {cuando}."
    ),
}

# --- Textos de los avisos al negocio -------------------------------------------------------------------------------
PLANTILLA_AVISO_NEGOCIO_RESERVA = "aviso_negocio_reserva"
PLANTILLA_AVISO_NEGOCIO_CANCELACION = "aviso_negocio_cancelacion"
PLANTILLA_AVISO_NEGOCIO_MOVIMIENTO = "aviso_negocio_movimiento"
PLANTILLA_AVISO_NEGOCIO_APROBACION = "aviso_negocio_aprobacion"
PLANTILLAS_AVISO_NEGOCIO = {
    PLANTILLA_AVISO_NEGOCIO_RESERVA: "Nueva reserva: {servicio} con {profesional}, {cuando}.",
    PLANTILLA_AVISO_NEGOCIO_CANCELACION: "Se canceló un turno de {servicio} con {profesional} del {cuando}.",
    PLANTILLA_AVISO_NEGOCIO_MOVIMIENTO: "Se movió un turno de {servicio} con {profesional}: ahora es el {cuando}.",
    PLANTILLA_AVISO_NEGOCIO_APROBACION: (
        "Hay un turno de {servicio} con {profesional}, {cuando}, pendiente de tu aprobación."
    ),
}

# --- Asuntos del email ---------------------------------------------------------------------------------------------
# Uno por plantilla de cliente y de negocio (un test lo compara con `plantillas_cliente()` y `plantillas_negocio()`).
# El asunto no lleva datos del cliente ni del turno: el detalle va en el cuerpo.
ASUNTOS_AVISO = {
    PLANTILLA_VENCIMIENTO: "Tu solicitud de turno venció",
    "turno_confirmado": "Tu turno fue confirmado",
    "turno_rechazado": "Tu solicitud de turno fue rechazada",
    "turno_cancelado": "Tu turno fue cancelado",
    "turno_movido": "Tu turno fue movido",
    PLANTILLA_CAMBIO_HORARIO_APROBADO: "Tu cambio de horario fue aprobado",
    PLANTILLA_CAMBIO_HORARIO_RECHAZADO: "Tu cambio de horario no fue aprobado",
    PLANTILLA_CAMBIO_HORARIO_VENCIDO: "Tu pedido de cambio de horario venció",
    PLANTILLA_AVISO_NEGOCIO_RESERVA: "Nueva reserva",
    PLANTILLA_AVISO_NEGOCIO_CANCELACION: "Se canceló un turno",
    PLANTILLA_AVISO_NEGOCIO_MOVIMIENTO: "Se movió un turno",
    PLANTILLA_AVISO_NEGOCIO_APROBACION: "Hay un turno pendiente de aprobación",
    "reserva_confirmada": "Tu turno quedó confirmado",
    "reserva_pendiente": "Recibimos tu solicitud de turno",
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


def asunto_aviso_turno(plantilla: str) -> str | None:
    """Asunto del email de `plantilla`, o `None` si no es un aviso de turno (p. ej. la sincronización de Calendar)."""
    return ASUNTOS_AVISO.get(plantilla)


def parametros_del_aviso(fila: Any) -> dict[str, Any]:
    """Los `params` de una fila del outbox como dict (la base los entrega como texto JSON o ya decodificados)."""
    crudo = fila["params"]
    datos = json.loads(crudo) if isinstance(crudo, str) else crudo
    return datos if isinstance(datos, dict) else {}


def inicio_del_aviso(fila: Any, destino: Any) -> datetime:
    """El inicio con que se encoló el aviso (un turno movido ya no empieza donde empezaba); si falta, el del turno
    (`destino["starts_at"]`, una fila de `resolve_outbox_delivery`)."""
    try:
        return datetime.fromisoformat(str(parametros_del_aviso(fila)["inicio"]))
    except (KeyError, ValueError):
        return destino["starts_at"]


def plantillas_conocidas() -> frozenset[str]:
    """Plantillas de turno que el reescaneo del job de vencimiento sabe enviar (no incluye cambio de horario)."""
    return frozenset(
        {PLANTILLA_VENCIMIENTO, PLANTILLA_RESERVA_CONFIRMADA, PLANTILLA_RESERVA_PENDIENTE, *PLANTILLAS_DASHBOARD}
    )


def plantillas_cliente() -> frozenset[str]:
    """Todas las plantillas de aviso al cliente que admite la base (las del reescaneo y las de cambio de horario)."""
    return plantillas_conocidas() | frozenset(PLANTILLAS_CAMBIO_HORARIO)


def plantillas_negocio() -> frozenset[str]:
    """Plantillas de aviso al negocio por un turno (la sincronización de Calendar no es un aviso: no está acá)."""
    return frozenset(PLANTILLAS_AVISO_NEGOCIO)


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
    los textos que mandan el dashboard y el bot en el envío inmediato y arma los del cambio de horario y los del
    negocio (en esos, un `profesional` ausente se dice "el profesional"); nunca lleva datos del cliente.
    """
    if plantilla == PLANTILLA_VENCIMIENTO:
        return mensaje_vencimiento(inicio, servicio)
    if plantilla in PLANTILLAS_DASHBOARD:
        return PLANTILLAS_DASHBOARD[plantilla].format(servicio=servicio, cuando=cuando_dashboard(inicio))
    if plantilla in PLANTILLAS_CAMBIO_HORARIO:
        return PLANTILLAS_CAMBIO_HORARIO[plantilla].format(servicio=servicio, cuando=cuando_dashboard(inicio))
    if plantilla in PLANTILLAS_AVISO_NEGOCIO:
        return PLANTILLAS_AVISO_NEGOCIO[plantilla].format(
            servicio=servicio, profesional=profesional or "el profesional", cuando=cuando_bot(inicio)
        )
    if plantilla in (PLANTILLA_RESERVA_CONFIRMADA, PLANTILLA_RESERVA_PENDIENTE):
        detalle = f"{servicio} con {profesional or 'el profesional'}, {cuando_bot(inicio)}."
        if plantilla == PLANTILLA_RESERVA_CONFIRMADA:
            return TEXTO_CONFIRMADO + "\n" + detalle
        return TEXTO_PENDIENTE.format(limite=cuando_bot(vence or inicio)) + "\n" + detalle
    return None
