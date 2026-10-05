"""Lectura y edición de `platform_settings` desde el panel (ARCHITECTURE.md §2.2, §2.11).

Solo se pueden editar las claves conocidas, cada una con su validador: una clave nueva se agrega acá junto con
la regla de su valor. Toda edición queda en `audit_log` con `tenant_id` null (no es una acción de un tenant).
"""

import json
from collections.abc import Callable
from typing import Any

from platform_audit import registrar
from platform_tenancy import admin_tx

from platform_admin._json import decodificar
from platform_admin.errores import SettingInvalido

CLAVE_PERIODO_GRACIA = "tenant_deletion_grace_period_days"


def _entero_positivo(clave: str, valor: Any) -> None:
    # bool es subclase de int: True no es un período de gracia válido.
    if isinstance(valor, bool) or not isinstance(valor, int) or valor <= 0:
        raise SettingInvalido(f"{clave} debe ser un entero positivo (días)")


_VALIDADORES: dict[str, Callable[[str, Any], None]] = {
    CLAVE_PERIODO_GRACIA: _entero_positivo,
}


async def listar_settings(pool: Any, *, actor_id: Any = None) -> dict[str, Any]:
    """Todos los `platform_settings` como `{clave: valor}` (valor ya decodificado)."""
    async with admin_tx(pool, actor_id) as conn:
        filas = await conn.fetch("select key, value from public.platform_settings order by key")
    return {f["key"]: decodificar(f["value"]) for f in filas}


async def editar_setting(pool: Any, clave: str, valor: Any, *, actor_id: Any) -> dict[str, Any]:
    """Edita (o crea) una clave conocida de `platform_settings` y audita el cambio.

    Devuelve `{"key", "desde", "hasta"}`. Valida antes de abrir la transacción: un valor inválido no toca la base.
    """
    validador = _VALIDADORES.get(clave) if isinstance(clave, str) else None
    if validador is None:
        raise SettingInvalido("clave de platform_settings desconocida o no editable")
    validador(clave, valor)
    async with admin_tx(pool, actor_id) as conn:
        previo = await conn.fetchval("select value from public.platform_settings where key = $1 for update", clave)
        desde = decodificar(previo)
        await conn.execute(
            "insert into public.platform_settings (key, value) values ($1, $2::jsonb) "
            "on conflict (key) do update set value = excluded.value",
            clave,
            json.dumps(valor),
        )
        await registrar(
            conn,
            "platform_settings_update",
            "platform_settings",
            None,
            {"key": clave, "desde": desde, "hasta": valor},
            tenant_id=None,
        )
    return {"key": clave, "desde": desde, "hasta": valor}
