"""Reserva de turnos por el bot (ARCHITECTURE.md §2.6, §7.1): servicio -> profesional -> horario -> confirmar.

- `flujo`: el handler `manejar_reserva` de la cadena del `Despachador` (va después de identidad).
- `repositorio`: consultas de catálogo y alta del booking (con la traducción del exclusion constraint).
- `calendario`: el evento de Google Calendar de un turno confirmado, después del commit.
"""

from bot_app.reserva.flujo import manejar_reserva

__all__ = ["manejar_reserva"]
