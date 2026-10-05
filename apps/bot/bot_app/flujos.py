"""Cadena de handlers de producción, en orden de prueba (el primero que atiende corta la cadena).

Identidad va primero: los demás flujos (reserva, consultas) solo ven mensajes de clientes ya identificados.
"""

from bot_app.despachador import Handler
from bot_app.identidad import manejar_identidad
from bot_app.reserva import manejar_reserva


def handlers_por_defecto() -> list[Handler]:
    return [manejar_identidad, manejar_reserva]
