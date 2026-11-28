"""Errores de la base que la web pública traduce a un 503 con código estable (nunca un 500 ni el detalle)."""

import asyncpg
from platform_tenancy import ERRORES_SERVICIO_OCUPADO

# Base caída, conexión cortada, protocolo roto o rol sin permiso: el cliente no puede hacer nada con el detalle.
ERRORES_BASE_NO_DISPONIBLE: tuple[type[Exception], ...] = (
    asyncpg.PostgresError,
    asyncpg.InterfaceError,
    OSError,
)

__all__ = ["ERRORES_BASE_NO_DISPONIBLE", "ERRORES_SERVICIO_OCUPADO"]
