"""platform_secrets — secrets de integraciones detrás de una interfaz propia (ARCHITECTURE.md §1.6, §4.3).

Las apps importan solo desde acá (`AlmacenSecretos`, `Secreto`, `crear_almacen`): la implementación sobre Supabase
Vault vive en `vault_supabase` y no se importa desde fuera de este paquete.
"""

from typing import Any

from platform_secrets.errores import AccesoSecretoDenegado, ErrorSecretos
from platform_secrets.interfaz import AlmacenSecretos, Secreto
from platform_secrets.vault_supabase import AlmacenVault


def crear_almacen(conn: Any) -> AlmacenSecretos:
    """Almacén de secrets ligado a la conexión de la transacción actual (`platform_tenancy`)."""
    return AlmacenVault(conn)


__all__ = [
    "AccesoSecretoDenegado",
    "AlmacenSecretos",
    "ErrorSecretos",
    "Secreto",
    "crear_almacen",
]
