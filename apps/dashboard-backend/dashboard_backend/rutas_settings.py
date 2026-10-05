"""Settings del local (`tenants.settings`, ARCHITECTURE.md §2.10, §3.2, §7.1).

  GET   /local/settings    cualquier staff activo del tenant
  PATCH /local/settings    manage_staff

Body del PATCH: `{"settings": {...}}`, una mezcla de primer nivel: cada clave enviada reemplaza a la existente y
`null` la elimina; lo que no se menciona queda como está. Guarda datos del local para el bot (dirección, horarios,
contacto), campos accesorios de `end_customers` y `booking_approval_timeout_hours` (entero 1-168, default 24 si no
está: lo valida el trigger de la base y su error sale como 422).

La escritura es solo `UPDATE tenants SET settings` sobre la fila del tenant de la sesión: es el único permiso de
escritura que `dashboard_service` tiene sobre `tenants` (grant por columna, §3.2). `status`, `plan`, `paid_until` y
demás son de Franco y no se pueden tocar desde acá aunque vengan en el body.
"""

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from dashboard_backend.dependencias import DependenciasDashboard, Transaccion
from dashboard_backend.errores_db import traducir_errores_db
from dashboard_backend.escritura_sql import no_encontrado
from dashboard_backend.esquemas_catalogo import SettingsParche

# `for update` serializa dos PATCH simultáneos del mismo tenant: la mezcla no pierde cambios.
_SQL_LEER = "select settings from public.tenants where id = $1"
_SQL_LEER_PARA_ACTUALIZAR = _SQL_LEER + " for update"
_SQL_ESCRIBIR = "update public.tenants set settings = $2::jsonb where id = $1 returning settings"


def _como_dict(valor: Any) -> dict[str, Any]:
    """asyncpg devuelve jsonb como texto salvo que se registre un codec; se acepta de las dos formas."""
    return json.loads(valor) if isinstance(valor, str) else dict(valor)


def crear_router_settings(deps: DependenciasDashboard) -> APIRouter:
    router = APIRouter(tags=["local"])
    Lectura = Annotated[Transaccion, Depends(deps.tx)]
    GestionaPersonal = Annotated[Transaccion, Depends(deps.tx_con_permiso("manage_staff"))]

    @router.get("/local/settings")
    async def leer_settings(tx: Lectura):
        fila = await tx.conn.fetchrow(_SQL_LEER, tx.sesion.tenant_id)
        if fila is None:
            raise no_encontrado("El local")
        return {"settings": _como_dict(fila["settings"])}

    @router.patch("/local/settings")
    async def modificar_settings(datos: SettingsParche, tx: GestionaPersonal):
        fila = await tx.conn.fetchrow(_SQL_LEER_PARA_ACTUALIZAR, tx.sesion.tenant_id)
        if fila is None:
            raise no_encontrado("El local")
        nuevos = _como_dict(fila["settings"])
        for clave, valor in datos.settings.items():
            if valor is None:
                nuevos.pop(clave, None)
            else:
                nuevos[clave] = valor
        with traducir_errores_db():
            escrita = await tx.conn.fetchrow(_SQL_ESCRIBIR, tx.sesion.tenant_id, json.dumps(nuevos))
        return {"settings": _como_dict(escrita["settings"])}

    return router
