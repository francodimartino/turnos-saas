"""Cálculo dinámico de disponibilidad para el bot (ARCHITECTURE.md §7.1).

- `huecos`: lógica pura (reglas semanales - ocupados -> huecos), sin base de datos.
- `consulta`: lectura de la base y `calcular_huecos(conn, profesional, servicio, rango)`.
"""

from disponibilidad.consulta import calcular_huecos
from disponibilidad.huecos import ZONA_HORARIA, ReglaSemanal, Ventana, huecos_libres

__all__ = ["ZONA_HORARIA", "ReglaSemanal", "Ventana", "calcular_huecos", "huecos_libres"]
