"""Catálogo tipado de permisos granulares del staff (§2.4): el único lugar de Python con las claves.

Las claves coinciden con las filas de `public.permissions` (el test de integración las compara con la base migrada)
y se exportan a `contratos/permisos.json` para que el frontend las tipe con el mismo catálogo.
El owner vivo tiene todo el catálogo sin asignación explícita.
"""

from enum import StrEnum


class Permiso(StrEnum):
    """Clave de permiso. Es un `str`: se pasa tal cual a la base y se serializa como su valor."""

    VER_TURNOS = "view_bookings"
    APROBAR_TURNOS = "approve_bookings"
    EDITAR_PRECIOS = "edit_prices"
    GESTIONAR_PERSONAL = "manage_staff"
    GESTIONAR_CLIENTES = "manage_customers"
    ELIMINAR_CLIENTES = "delete_customers"
    GESTIONAR_AGENDA = "manage_schedule"


def claves_de_permisos() -> list[str]:
    """Las claves del catálogo, ordenadas (el orden del contrato exportado)."""
    return sorted(p.value for p in Permiso)
