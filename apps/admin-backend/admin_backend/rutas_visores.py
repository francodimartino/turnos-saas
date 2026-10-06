"""Visores y carga de integraciones del panel (ARCHITECTURE.md §2.11.1, §2.11.5, §2.11.7).

Todo exige un platform admin con `aal2` (`deps.sesion`) y corre con el rol Postgres `platform_admin`.

  GET /tenants/{id}/audit-log                         audit_log del tenant (filtros: action, actor_type, desde,
                                                      hasta; paginado con limit/offset)
  GET /notificaciones/problemas?limit=                notificaciones `failed` o `pending` hace más de 1 hora
                                                      (todos los tenants) y limpiezas de Calendar fallidas
  PUT /tenants/{id}/integraciones/{tipo}              carga la credencial de la integración del tenant
                                                      (solo `telegram_bot`); body `{config?, secret}`

El `secret` entra por el body, viaja hasta Vault y no vuelve: la respuesta es solo `{integration_id, tenant_id,
integration_type, webhook_key}`. Nunca se loguea (el log de requests no incluye cuerpos) ni se repite en errores.
"""

import datetime
import uuid
from collections.abc import Callable
from typing import Annotated, Any

import platform_admin as pa
from fastapi import APIRouter, Depends, Query
from platform_auth import SesionAdmin
from platform_secrets import Secreto
from pydantic import BaseModel, ConfigDict

from admin_backend.dependencias import DependenciasAdmin


class IntegracionIn(BaseModel):
    # `Any` a propósito: la validación (y su mensaje) es la del dominio, que nunca nombra el valor recibido.
    model_config = ConfigDict(extra="ignore")

    config: Any = None
    secret: Any = None


def crear_router_visores(deps: DependenciasAdmin, obtener_pool: Callable[[], Any]) -> APIRouter:
    router = APIRouter(tags=["visores"], dependencies=[Depends(deps.sesion)])
    Admin = Annotated[SesionAdmin, Depends(deps.sesion)]  # noqa: N806

    @router.get("/tenants/{tenant_id}/audit-log")
    async def audit_log(
        tenant_id: uuid.UUID,
        admin: Admin,
        action: str | None = None,
        actor_type: str | None = None,
        desde: datetime.datetime | None = None,
        hasta: datetime.datetime | None = None,
        limit: int = Query(100, ge=1, le=pa.auditoria.LIMITE_MAXIMO),
        offset: int = Query(0, ge=0),
    ) -> list[pa.EntradaAudit]:
        return await pa.listar_audit_log(
            obtener_pool(),
            tenant_id,
            actor_id=admin.user_id,
            action=action,
            actor_type=actor_type,
            desde=desde,
            hasta=hasta,
            limit=limit,
            offset=offset,
        )

    @router.get("/notificaciones/problemas")
    async def problemas_notificaciones(
        admin: Admin, limit: int = Query(200, ge=1, le=pa.auditoria.LIMITE_MAXIMO)
    ) -> pa.ProblemasNotificaciones:
        return await pa.visor_problemas_notificaciones(obtener_pool(), actor_id=admin.user_id, limit=limit)

    @router.put("/tenants/{tenant_id}/integraciones/{tipo}")
    async def cargar_integracion(
        tenant_id: uuid.UUID, tipo: str, datos: IntegracionIn, admin: Admin
    ) -> pa.ResultadoIntegracion:
        secreto = Secreto(datos.secret) if isinstance(datos.secret, str) else datos.secret
        return await pa.cargar_integracion_tenant(
            obtener_pool(),
            tenant_id,
            tipo,
            {} if datos.config is None else datos.config,
            secreto,
            actor_id=admin.user_id,
        )

    return router
