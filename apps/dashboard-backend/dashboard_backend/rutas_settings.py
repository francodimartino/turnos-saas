"""Settings del local (`tenants.settings`, ARCHITECTURE.md §2.10, §3.2, §7.1).

  GET   /local/settings    cualquier staff activo del tenant
  PATCH /local/settings    manage_schedule

Body del PATCH: `{"settings": {...}}`, una mezcla de primer nivel: cada clave enviada reemplaza a la existente y
`null` la elimina; lo que no se menciona queda como está. Guarda datos del local para el bot (dirección, horarios,
contacto), campos accesorios de `end_customers` y los límites de reserva. Cada clave conocida se valida por tipo y
largo (422 que nombra la clave); el rango final de los enteros lo vuelve a hacer cumplir el trigger de la base.

Este módulo solo traduce HTTP: las reglas y el SQL están en `settings_local`, que no conoce FastAPI. Es el modelo de
router de módulo del dashboard-backend: `router` a nivel de módulo, registrado con `include_router`, y la transacción
(pool, sesión, permiso) tomada con `Depends` de `request.app.state` (ver `dependencias`).

La escritura es solo `UPDATE tenants SET settings` sobre la fila del tenant de la sesión: es el único permiso de
escritura que `dashboard_service` tiene sobre `tenants` (grant por columna, §3.2). `status`, `plan`, `paid_until` y
demás son de Franco y no se pueden tocar desde acá aunque vengan en el body.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from dashboard_backend import settings_local
from dashboard_backend.dependencias import Transaccion, transaccion, transaccion_con_permiso
from dashboard_backend.errores_db import traducir_errores_db
from dashboard_backend.escritura_sql import no_encontrado
from dashboard_backend.esquemas_catalogo import MAX_BYTES_SETTINGS, SettingsParche

router = APIRouter(tags=["local"])

# Con nombre para poder reemplazarla en `app.dependency_overrides` (cada llamada a la fábrica crea otra función).
tx_gestiona_agenda = transaccion_con_permiso("manage_schedule")

Lectura = Annotated[Transaccion, Depends(transaccion)]
GestionaAgenda = Annotated[Transaccion, Depends(tx_gestiona_agenda)]


@router.get("/local/settings")
async def leer_settings(tx: Lectura):
    try:
        return {"settings": await settings_local.leer_settings(tx.conn, tx.sesion.tenant_id)}
    except settings_local.ErrorLocalInexistente:
        raise no_encontrado("El local") from None


@router.patch("/local/settings")
async def modificar_settings(datos: SettingsParche, tx: GestionaAgenda):
    try:
        with traducir_errores_db():
            nuevos = await settings_local.modificar_settings(tx.conn, tx.sesion.tenant_id, datos.settings)
    except settings_local.ErrorLocalInexistente:
        raise no_encontrado("El local") from None
    except settings_local.ErrorSettingsDemasiadoGrandes:
        raise HTTPException(
            status_code=422,
            detail={
                "codigo": "settings_demasiado_grandes",
                "mensaje": f"los settings del local superarían el máximo de {MAX_BYTES_SETTINGS // 1000} KB",
            },
        ) from None
    return {"settings": nuevos}
