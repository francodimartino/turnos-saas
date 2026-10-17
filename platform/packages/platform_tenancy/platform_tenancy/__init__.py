"""platform_tenancy — ver ARCHITECTURE.md §4.3.

Cliente de DB sobre asyncpg (conexión directa a Postgres, no el cliente de Supabase) que corre cada
request dentro de una transacción explícita y fija el contexto que lee la RLS (`app.current_tenant_id`,
`app.current_actor_id`) con `set local`, que solo vive dentro de esa transacción.

El `tenant_id` y el `actor_id` salen siempre de la identidad ya verificada por el backend (JWT, bot,
sesión de admin), nunca del cuerpo del request (§3.1.3).
"""

from platform_tenancy.errores import ContextoTenantInvalido
from platform_tenancy.pool import (
    ACQUIRE_TIMEOUT_DEFECTO,
    COMMAND_TIMEOUT_DEFECTO,
    CONNECT_TIMEOUT_DEFECTO,
    TAMANOS_POOL,
    PoolConTimeout,
    crear_pool,
    opciones_pool,
)
from platform_tenancy.saturacion import (
    CODIGO_SERVICIO_OCUPADO,
    ERRORES_SERVICIO_OCUPADO,
    MENSAJE_SERVICIO_OCUPADO,
    cuerpo_servicio_ocupado,
)
from platform_tenancy.transacciones import admin_tx, readonly_tx, tenant_tx

__all__ = [
    "CODIGO_SERVICIO_OCUPADO",
    "ERRORES_SERVICIO_OCUPADO",
    "MENSAJE_SERVICIO_OCUPADO",
    "cuerpo_servicio_ocupado",
    "ACQUIRE_TIMEOUT_DEFECTO",
    "COMMAND_TIMEOUT_DEFECTO",
    "CONNECT_TIMEOUT_DEFECTO",
    "TAMANOS_POOL",
    "PoolConTimeout",
    "ContextoTenantInvalido",
    "admin_tx",
    "crear_pool",
    "opciones_pool",
    "readonly_tx",
    "tenant_tx",
]
