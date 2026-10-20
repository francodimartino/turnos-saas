"""Textos de turnos para el calendario externo (ARCHITECTURE.md §4.3, §7.1).

`platform_calendar` es de plataforma y no sabe de turnos: recibe un `EventoCalendario` ya armado y, para anonimizar,
un `TextoNeutro`. Lo propio de turnos (qué dice el título, qué lleva la descripción, cómo se muestra un cliente que
pidió borrar sus datos) vive acá, para que el dashboard, el bot y la purga escriban lo mismo.
"""

from datetime import datetime

from platform_calendar import EventoCalendario, TextoNeutro

CLIENTE_ANONIMIZADO = "(cliente anonimizado)"
CLIENTE_SIN_NOMBRE = "Cliente"

# Con lo que se reescribe un evento cuando se borran los datos del cliente o se purga el tenant.
TEXTO_EVENTO_ANONIMIZADO = TextoNeutro("Turno (cliente anonimizado)", "Datos del cliente eliminados.")


def evento_de_turno(
    *,
    servicio: str,
    cliente_nombre: str | None,
    cliente_anonimizado: bool,
    inicio: datetime,
    fin: datetime,
    id_externo: str | None = None,
) -> EventoCalendario:
    """Evento de calendario de un turno: servicio y nombre del cliente, nunca su teléfono.

    Un cliente anonimizado no tiene datos: el evento no los inventa. Sin nombre se muestra `Cliente`.
    """
    cliente = CLIENTE_ANONIMIZADO if cliente_anonimizado else (cliente_nombre or CLIENTE_SIN_NOMBRE)
    return EventoCalendario(
        titulo=f"{servicio} — {cliente}",
        descripcion=f"Servicio: {servicio}\nCliente: {cliente}",
        inicio=inicio,
        fin=fin,
        id_externo=id_externo,
    )
