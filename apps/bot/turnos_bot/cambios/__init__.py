"""Cancelar y mover un turno por el bot (ARCHITECTURE.md §2.6, §7.1).

- `flujo`: el handler `manejar_cambios` de la cadena del `Despachador` (va después de identidad y reserva).
- `repositorio`: lectura del turno propio del cliente (aislamiento por tenant y por persona).
- `calendario`: el evento de Google Calendar del turno cancelado o movido, después del commit.

La política (plazo del negocio, mover con o sin aprobación, un solo cambio pendiente) es de `turnos_dominio.cambios`:
el bot solo la invoca y traduce sus errores a mensajes.
"""

from turnos_bot.cambios.flujo import manejar_cambios

__all__ = ["manejar_cambios"]
