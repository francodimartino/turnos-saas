"""Visores y carga de integraciones del panel (ARCHITECTURE.md §2.11.1, §2.11.5, §2.11.7).

Todo exige un platform admin con `aal2` (`deps.sesion`) y corre con el rol Postgres `platform_admin`. Router de módulo
(`router`, registrado con `include_router`): sesión y pool se toman con `Depends` de `app.state` (`dependencias`).

  GET /tenants/{id}/audit-log                         audit_log del tenant (filtros: action, actor_type, desde,
                                                      hasta; paginado con limit/offset)
  GET /notificaciones/problemas?limit=                notificaciones `failed` o `pending` hace más de 1 hora
                                                      (todos los tenants) y limpiezas de Calendar fallidas
  GET /tenants/{id}/audit-log/verificar-cadena        verifica la cadena de hashes del audit_log del tenant
  GET /plataforma/audit-log/verificar-cadena          ídem para la cadena de plataforma (tenant_id null)
  POST /tenants/{id}/limpiezas-calendar/descartar     descarta una limpieza de Calendar pendiente; body
                                                      `{event_id, motivo}`; 404 si no está pendiente, 422 si falta
                                                      el motivo
  PUT /tenants/{id}/integraciones/{tipo}              carga la credencial de la integración del tenant
                                                      (solo `telegram_bot`); body `{config?, secret}`

El `secret` entra por el body, viaja hasta Vault y no vuelve: la respuesta es solo `{integration_id, tenant_id,
integration_type, webhook_key}`. Nunca se loguea (el log de requests no incluye cuerpos) ni se repite en errores.
"""

import datetime
import logging
import uuid
from collections.abc import Callable
from typing import Annotated, Any

import platform_admin as pa
from fastapi import APIRouter, Depends, Query
from platform_auth import SesionAdmin
from platform_secrets import Secreto
from pydantic import BaseModel, ConfigDict

from admin_backend.dependencias import pool_admin, sesion_admin
from admin_backend.esquemas_salida import CadenaAuditOut, DescarteLimpiezaOut

_log = logging.getLogger("admin_backend")


class IntegracionIn(BaseModel):
    # `Any` a propósito: la validación (y su mensaje) es la del dominio, que nunca nombra el valor recibido.
    model_config = ConfigDict(extra="ignore")

    config: Any = None
    secret: Any = None


class DescarteLimpiezaIn(BaseModel):
    # `Any` a propósito: la validación (y su mensaje) es la del dominio, que nunca repite lo recibido.
    model_config = ConfigDict(extra="ignore")

    event_id: Any = None
    motivo: Any = None


def _respuesta_cadena(res: pa.ResultadoCadena) -> CadenaAuditOut:
    return CadenaAuditOut(
        tenant_id=None if res.tenant_id is None else str(res.tenant_id),
        integra=res.integra,
        fila_invalida_id=None if res.fila_invalida_id is None else str(res.fila_invalida_id),
        fila_invalida_chain_seq=res.fila_invalida_chain_seq,
    )


router = APIRouter(tags=["visores"], dependencies=[Depends(sesion_admin)])

Admin = Annotated[SesionAdmin, Depends(sesion_admin)]
Pool = Annotated[Callable[[], Any], Depends(pool_admin)]


@router.get("/tenants/{tenant_id}/audit-log")
async def audit_log(
    tenant_id: uuid.UUID,
    admin: Admin,
    obtener_pool: Pool,
    action: str | None = None,
    actor_type: str | None = None,
    desde: datetime.datetime | None = None,
    hasta: datetime.datetime | None = None,
    limit: int = Query(100, ge=1, le=pa.auditoria.LIMITE_MAXIMO),
    offset: int = Query(0, ge=0, le=2_147_483_647),
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


@router.get("/tenants/{tenant_id}/audit-log/verificar-cadena")
async def verificar_cadena_tenant(tenant_id: uuid.UUID, admin: Admin, obtener_pool: Pool) -> CadenaAuditOut:
    return _respuesta_cadena(await pa.verificar_cadena_audit(obtener_pool(), tenant_id, actor_id=admin.user_id))


@router.get("/plataforma/audit-log/verificar-cadena")
async def verificar_cadena_plataforma(admin: Admin, obtener_pool: Pool) -> CadenaAuditOut:
    return _respuesta_cadena(await pa.verificar_cadena_audit(obtener_pool(), None, actor_id=admin.user_id))


@router.post("/tenants/{tenant_id}/limpiezas-calendar/descartar")
async def descartar_limpieza(
    tenant_id: uuid.UUID, datos: DescarteLimpiezaIn, admin: Admin, obtener_pool: Pool
) -> DescarteLimpiezaOut:
    await pa.descartar_limpieza_calendar(
        obtener_pool(), tenant_id, datos.event_id, datos.motivo, actor_id=admin.user_id
    )
    # La base registra el descarte con actor_type platform_admin y actor_id null; el admin concreto queda acá.
    _log.info(
        "limpieza_calendar_descartada",
        extra={"admin_id": str(admin.user_id), "tenant_id": str(tenant_id), "actor_type": "platform_admin"},
    )
    return DescarteLimpiezaOut(tenant_id=str(tenant_id), event_id=datos.event_id, estado="descartada")


@router.get("/notificaciones/problemas")
async def problemas_notificaciones(
    admin: Admin, obtener_pool: Pool, limit: int = Query(200, ge=1, le=pa.auditoria.LIMITE_MAXIMO)
) -> pa.ProblemasNotificaciones:
    return await pa.visor_problemas_notificaciones(obtener_pool(), actor_id=admin.user_id, limit=limit)


@router.put("/tenants/{tenant_id}/integraciones/{tipo}")
async def cargar_integracion(
    tenant_id: uuid.UUID, tipo: str, datos: IntegracionIn, admin: Admin, obtener_pool: Pool
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
