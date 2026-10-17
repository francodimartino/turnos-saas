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
from platform_admin.errores import ReduccionSinConfirmar, SettingInvalido

CLAVE_PERIODO_GRACIA = "tenant_deletion_grace_period_days"
# Mismo piso que el trigger de la base (23514): acá se valida antes para responder 422 sin tocar la base.
GRACIA_MINIMA_DIAS = 30

_CHECK_VIOLATION = "23514"


def _periodo_de_gracia(clave: str, valor: Any) -> None:
    # bool es subclase de int: True no es un período de gracia válido.
    if isinstance(valor, bool) or not isinstance(valor, int) or valor < GRACIA_MINIMA_DIAS:
        raise SettingInvalido(f"{clave} debe ser un entero de al menos {GRACIA_MINIMA_DIAS} días")


_VALIDADORES: dict[str, Callable[[str, Any], None]] = {
    CLAVE_PERIODO_GRACIA: _periodo_de_gracia,
}

# Claves numéricas donde bajar el valor vigente exige confirmación explícita (acorta un plazo de recuperación).
_CONFIRMAR_SI_BAJA = frozenset({CLAVE_PERIODO_GRACIA})


async def listar_settings(pool: Any, *, actor_id: Any = None) -> dict[str, Any]:
    """Todos los `platform_settings` como `{clave: valor}` (valor ya decodificado)."""
    async with admin_tx(pool, actor_id) as conn:
        filas = await conn.fetch("select key, value from public.platform_settings order by key")
    return {f["key"]: decodificar(f["value"]) for f in filas}


async def editar_setting(
    pool: Any, clave: str, valor: Any, *, actor_id: Any, confirmar_reduccion: bool = False
) -> dict[str, Any]:
    """Edita (o crea) una clave conocida de `platform_settings` y audita el cambio.

    Devuelve `{"key", "desde", "hasta"}`. Valida antes de abrir la transacción: un valor inválido no toca la base.
    Para las claves de `_CONFIRMAR_SI_BAJA`, un valor menor al vigente exige `confirmar_reduccion=True`; sin ella
    lanza `ReduccionSinConfirmar` y no escribe. Un 23514 de la base (trigger del piso) se traduce a `SettingInvalido`.
    """
    validador = _VALIDADORES.get(clave) if isinstance(clave, str) else None
    if validador is None:
        raise SettingInvalido("clave de platform_settings desconocida o no editable")
    validador(clave, valor)
    try:
        async with admin_tx(pool, actor_id) as conn:
            previo = await conn.fetchval("select value from public.platform_settings where key = $1 for update", clave)
            desde = decodificar(previo)
            baja = (
                clave in _CONFIRMAR_SI_BAJA and isinstance(desde, int) and not isinstance(desde, bool) and valor < desde
            )
            if baja and confirmar_reduccion is not True:
                raise ReduccionSinConfirmar(clave, desde, valor)
            await conn.execute(
                "insert into public.platform_settings (key, value) values ($1, $2::jsonb) "
                "on conflict (key) do update set value = excluded.value",
                clave,
                json.dumps(valor),
            )
            metadata: dict[str, Any] = {"key": clave, "desde": desde, "hasta": valor}
            if baja:
                metadata["reduccion_confirmada"] = True
            await registrar(conn, "platform_settings_update", "platform_settings", None, metadata, tenant_id=None)
    except ReduccionSinConfirmar:
        raise
    except Exception as exc:
        if getattr(exc, "sqlstate", None) == _CHECK_VIOLATION:
            raise SettingInvalido(f"{clave} no cumple el mínimo de {GRACIA_MINIMA_DIAS} días") from None
        raise
    return {"key": clave, "desde": desde, "hasta": valor}
