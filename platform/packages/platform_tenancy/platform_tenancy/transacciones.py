"""Transacciones con contexto de tenant/actor para la RLS (§3.1, §4.3)."""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

from platform_tenancy.errores import ContextoTenantInvalido

# set_config(..., true) es la forma parametrizable de `set local`: vale solo hasta el fin de la
# transacción. `set local` como tal no admite parámetros y obligaría a interpolar strings.
_SQL_TENANT_Y_ACTOR = (
    "select set_config('app.current_tenant_id', $1, true), set_config('app.current_actor_id', $2, true)"
)
_SQL_SOLO_ACTOR = "select set_config('app.current_actor_id', $1, true)"


def _como_uuid(valor: Any, nombre: str) -> str:
    if isinstance(valor, uuid.UUID):
        return str(valor)
    if isinstance(valor, str):
        try:
            return str(uuid.UUID(valor))
        except ValueError:
            pass
    raise ContextoTenantInvalido(f"{nombre} inválido: se esperaba un UUID y llegó {valor!r}")


def _actor_o_vacio(actor_id: Any) -> str:
    # Vacío = sin actor (la RLS y las funciones lo leen con nullif(..., '')).
    return "" if actor_id is None else _como_uuid(actor_id, "actor_id")


@asynccontextmanager
async def _transaccion(pool: Any, inicio: str, contexto: tuple[str, tuple] | None) -> AsyncIterator[Any]:
    async with pool.acquire() as conn:
        await conn.execute(inicio)
        try:
            if contexto is not None:
                sql, args = contexto
                await conn.execute(sql, *args)
            yield conn
            await conn.execute("COMMIT")
        except BaseException:
            # Si el rollback también falla, se propaga el error original, no este.
            with suppress(Exception):
                await conn.execute("ROLLBACK")
            raise


@asynccontextmanager
async def tenant_tx(pool: Any, tenant_id: Any, actor_id: Any = None) -> AsyncIterator[Any]:
    """Transacción explícita con `app.current_tenant_id` y `app.current_actor_id` seteados.

    Sin tenant_id válido falla con `ContextoTenantInvalido` antes de tomar una conexión.
    Hace COMMIT al salir y ROLLBACK ante cualquier excepción (que se propaga).
    """
    tenant = _como_uuid(tenant_id, "tenant_id")
    actor = _actor_o_vacio(actor_id)
    async with _transaccion(pool, "BEGIN", (_SQL_TENANT_Y_ACTOR, (tenant, actor))) as conn:
        yield conn


@asynccontextmanager
async def admin_tx(pool: Any, actor_id: Any = None) -> AsyncIterator[Any]:
    """Transacción sin contexto de tenant, para el rol `platform_admin` (policy explícita, §3.1).

    Si se pasa `actor_id` (la identidad verificada del admin) se setea para la auditoría.
    """
    actor = _actor_o_vacio(actor_id)
    contexto = (_SQL_SOLO_ACTOR, (actor,)) if actor else None
    async with _transaccion(pool, "BEGIN", contexto) as conn:
        yield conn


@asynccontextmanager
async def readonly_tx(pool: Any, tenant_id: Any, actor_id: Any = None) -> AsyncIterator[Any]:
    """Transacción de solo lectura con el tenant fijado, para `platform_admin_readonly` (impersonación).

    Setea `app.current_tenant_id` (el tenant que se está viendo) y `app.current_actor_id`. Sin tenant_id válido
    falla con `ContextoTenantInvalido` antes de tomar una conexión. Además de los grants del rol,
    `BEGIN READ ONLY` hace que Postgres rechace cualquier escritura.
    """
    tenant = _como_uuid(tenant_id, "tenant_id")
    actor = _actor_o_vacio(actor_id)
    async with _transaccion(pool, "BEGIN READ ONLY", (_SQL_TENANT_Y_ACTOR, (tenant, actor))) as conn:
        yield conn
