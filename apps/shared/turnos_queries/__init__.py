"""Funciones de consulta compartidas por dashboard-backend y admin-backend (ARCHITECTURE.md §5.4)."""

from turnos_queries.consultas import (
    detalle_booking,
    listar_bookings,
    listar_clientes,
    listar_packs,
    listar_profesionales,
    listar_servicios,
)

__all__ = [
    "detalle_booking",
    "listar_bookings",
    "listar_clientes",
    "listar_packs",
    "listar_profesionales",
    "listar_servicios",
]
