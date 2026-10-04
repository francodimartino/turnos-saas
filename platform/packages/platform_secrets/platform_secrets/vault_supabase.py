"""Implementación de `AlmacenSecretos` sobre Supabase Vault.

Solo llama a las funciones SECURITY DEFINER de la base (`get_integration_secret`, `store_tenant_integration`,
...): nunca lee `vault.*` ni escribe `tenant_integrations` directo. Es el único módulo que conoce a Vault; el
resto del código importa la interfaz (`platform_secrets.interfaz`). No usa el SDK de Supabase: habla con
Postgres por la conexión asyncpg de la transacción.

Los errores de la base se traducen a errores propios sin causa encadenada ni valores: ningún secret termina
en un traceback ni en un log.
"""

import json
import uuid
from typing import Any

import asyncpg

from platform_secrets.errores import AccesoSecretoDenegado, ErrorSecretos
from platform_secrets.interfaz import AlmacenSecretos, Secreto


def _texto(secret: "str | Secreto") -> str:
    valor = secret.revelar() if isinstance(secret, Secreto) else secret
    if not isinstance(valor, str) or valor == "":
        raise ValueError("el secret es obligatorio y debe ser texto")
    return valor


def _traducir(error: asyncpg.PostgresError) -> ErrorSecretos:
    if isinstance(error, asyncpg.InsufficientPrivilegeError):
        return AccesoSecretoDenegado("acceso denegado a la integración o al secret")
    return ErrorSecretos(f"error de secrets (sqlstate {getattr(error, 'sqlstate', None)})")


class AlmacenVault(AlmacenSecretos):
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def _llamar(self, sql: str, *args: Any) -> Any:
        try:
            return await self._conn.fetchval(sql, *args)
        except asyncpg.PostgresError as error:
            raise _traducir(error) from None

    async def obtener(self, integration_id: uuid.UUID) -> Secreto | None:
        valor = await self._llamar("select public.get_integration_secret($1)", integration_id)
        return None if valor is None else Secreto(valor)

    async def buscar(self, integration_type: str, professional_id: uuid.UUID | None = None) -> uuid.UUID | None:
        return await self._llamar("select public.find_integration($1, $2)", integration_type, professional_id)

    async def guardar(
        self, tenant_id: uuid.UUID, integration_type: str, config: dict[str, Any] | None, secret: "str | Secreto"
    ) -> uuid.UUID:
        return await self._llamar(
            "select public.store_tenant_integration($1, $2, $3, $4)",
            tenant_id,
            integration_type,
            json.dumps(config or {}),
            _texto(secret),
        )

    async def guardar_profesional(
        self,
        professional_id: uuid.UUID,
        config: dict[str, Any] | None,
        secret: "str | Secreto",
        integration_type: str = "google_calendar",
    ) -> uuid.UUID:
        return await self._llamar(
            "select public.store_professional_integration($1, $2, $3, $4)",
            integration_type,
            professional_id,
            json.dumps(config or {}),
            _texto(secret),
        )

    async def eliminar_profesional(self, professional_id: uuid.UUID) -> bool:
        return bool(await self._llamar("select public.delete_professional_integration($1)", professional_id))
