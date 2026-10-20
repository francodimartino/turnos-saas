"""Cadena de handlers de producción, en orden de prueba (el primero que atiende corta la cadena).

Identidad va primero: los demás flujos (reserva, consultas) solo ven mensajes de clientes ya identificados.
"""

from turnos_bot.consultas import manejar_consultas
from turnos_bot.despachador import Handler
from turnos_bot.identidad import manejar_identidad
from turnos_bot.reserva import manejar_reserva


def handlers_por_defecto() -> list[Handler]:
    return [manejar_identidad, manejar_reserva, manejar_consultas]
