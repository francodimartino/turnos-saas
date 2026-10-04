"""platform_tenancy — ver ARCHITECTURE.md §4.3.

Cliente de DB sobre asyncpg (conexión directa a Postgres, no el cliente de Supabase) que corre cada
request dentro de una transacción explícita y fija el contexto que lee la RLS (`app.current_tenant_id`,
`app.current_actor_id`) con `set local`, que solo vive dentro de esa transacción.

El `tenant_id` y el `actor_id` salen siempre de la identidad ya verificada por el backend (JWT, bot,
sesión de admin), nunca del cuerpo del request (§3.1.3).
"""

from platform_tenancy.errores import ContextoTenantInvalido
from platform_tenancy.pool import crear_pool, opciones_pool
from platform_tenancy.transacciones import admin_tx, readonly_tx, tenant_tx

__all__ = [
    "ContextoTenantInvalido",
    "admin_tx",
    "crear_pool",
    "opciones_pool",
    "readonly_tx",
    "tenant_tx",
]
