"""Dominio de los settings del local (`tenants.settings`, ARCHITECTURE.md §2.10, §3.2): sin FastAPI.

Las reglas y la orquestación de la lectura y la mezcla de settings viven acá; `rutas_settings` solo traduce HTTP y
los errores de dominio de este módulo (`ErrorLocalInexistente`, `ErrorSettingsDemasiadoGrandes`) a respuestas.

La mezcla es de primer nivel: cada clave enviada reemplaza a la existente y `None` la elimina; lo que no se menciona
queda como está. La escritura es solo `UPDATE tenants SET settings` sobre la fila del tenant de la sesión (único grant
de escritura de `dashboard_service` sobre `tenants`). El tope de `MAX_BYTES_SETTINGS` se aplica sobre el resultado de
la mezcla, no solo sobre el parche: sin eso, parches chicos repetidos lo inflarían.
"""

import json
from typing import Any
from uuid import UUID

from platform_audit import registrar

from dashboard_backend.esquemas_catalogo import MAX_BYTES_SETTINGS

# `for update` serializa dos PATCH simultáneos del mismo tenant: la mezcla no pierde cambios.
SQL_LEER = "select settings from public.tenants where id = $1"
SQL_LEER_PARA_ACTUALIZAR = SQL_LEER + " for update"
SQL_ESCRIBIR = "update public.tenants set settings = $2::jsonb where id = $1 returning settings"

__all__ = [
    "MAX_BYTES_SETTINGS",
    "SQL_ESCRIBIR",
    "SQL_LEER",
    "SQL_LEER_PARA_ACTUALIZAR",
    "ErrorLocalInexistente",
    "ErrorSettingsDemasiadoGrandes",
    "leer_settings",
    "mezclar",
    "modificar_settings",
]


class ErrorLocalInexistente(Exception):
    """La fila del tenant no existe (o la sesión no la ve)."""


class ErrorSettingsDemasiadoGrandes(Exception):
    """El resultado de la mezcla superaría `MAX_BYTES_SETTINGS`."""


def como_dict(valor: Any) -> dict[str, Any]:
    """asyncpg devuelve jsonb como texto salvo que se registre un codec; se acepta de las dos formas."""
    return json.loads(valor) if isinstance(valor, str) else dict(valor)


def mezclar(actuales: dict[str, Any], cambios: dict[str, Any]) -> dict[str, Any]:
    """Settings resultantes de aplicar `cambios` sobre `actuales` (no modifica los argumentos)."""
    nuevos = dict(actuales)
    for clave, valor in cambios.items():
        if valor is None:
            nuevos.pop(clave, None)
        else:
            nuevos[clave] = valor
    if len(json.dumps(nuevos)) > MAX_BYTES_SETTINGS:
        raise ErrorSettingsDemasiadoGrandes
    return nuevos


async def leer_settings(conn: Any, tenant_id: UUID) -> dict[str, Any]:
    fila = await conn.fetchrow(SQL_LEER, tenant_id)
    if fila is None:
        raise ErrorLocalInexistente
    return como_dict(fila["settings"])


async def modificar_settings(conn: Any, tenant_id: UUID, cambios: dict[str, Any]) -> dict[str, Any]:
    """Aplica la mezcla y la audita en la misma transacción: si el audit falla, el cambio se revierte.

    El registro lleva solo las claves tocadas, nunca los valores.
    """
    fila = await conn.fetchrow(SQL_LEER_PARA_ACTUALIZAR, tenant_id)
    if fila is None:
        raise ErrorLocalInexistente
    nuevos = mezclar(como_dict(fila["settings"]), cambios)
    escrita = await conn.fetchrow(SQL_ESCRIBIR, tenant_id, json.dumps(nuevos))
    await registrar(conn, "settings_updated", "tenants", tenant_id, {"claves": sorted(cambios)})
    return como_dict(escrita["settings"])
