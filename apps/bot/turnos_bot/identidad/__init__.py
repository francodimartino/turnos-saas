"""Identidad del cliente final: alta por primer contacto, dedup por teléfono, recontacto y derechos (§2.3, §2.8).

Los flujos de reserva y consultas piden al cliente con `obtener_o_crear_end_customer(ctx)`.
"""

from turnos_bot.identidad.flujo import manejar_identidad, obtener_o_crear_end_customer
from turnos_bot.identidad.repositorio import EndCustomer

__all__ = ["EndCustomer", "manejar_identidad", "obtener_o_crear_end_customer"]
