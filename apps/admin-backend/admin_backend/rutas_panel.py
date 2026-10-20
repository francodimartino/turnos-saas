"""Endpoints del panel: tenants (alta, ficha, ciclo de vida, invitación), `platform_settings` y facturación manual.

Todo exige un platform admin con `aal2` (`deps.sesion`). El `actor_id` de la auditoría es siempre el `user_id` del
JWT verificado, nunca algo del body. Las operaciones viven en `platform_admin` y corren con el rol Postgres
`platform_admin` (`admin_tx`); acá solo se traduce HTTP <-> dominio. Es un router de módulo (`router`, registrado con
`include_router`): la sesión, el pool y el cliente de la Auth Admin API se toman con `Depends` de `app.state`
(`dependencias`). Los errores de dominio se convierten a 404/409/422
(`errores.py`); una transición inválida es 409.

  GET  /tenants?status=                         lista
  POST /tenants                                 alta (name, slug, settings?, owner_email, owner_nombre) + invitación
  GET  /tenants/{id}                            ficha (con owner_nombre y owner_email)
  POST /tenants/{id}/suspender                  active -> suspended
  POST /tenants/{id}/levantar-suspension        suspended -> active
  POST /tenants/{id}/dar-de-baja                active|suspended -> inactive (arranca el período de gracia)
  POST /tenants/{id}/reactivar                  inactive -> active
  POST /tenants/{id}/reenviar-invitacion        reintenta la invitación del owner (email guardado o corregido;
                                                owner_nombre opcional: la base no guarda el de una pendiente)
  GET  /tenants/{id}/facturacion                paid_until y billing_notes
  PUT  /tenants/{id}/facturacion                edita lo que venga en el body (null borra)
  GET  /settings                                platform_settings
  PUT  /settings/{clave}                        edita una clave conocida (body: valor, confirmar_reduccion?)

Nombre y email del owner se ven siempre en la lista y la ficha de tenants (`owner_nombre`, `owner_email`; los lee
`platform_admin` con `public.get_tenant_staff_identity`: de la cuenta vinculada, o el email de la invitación pendiente
con nombre null). Las respuestas de alta y reenvío no los repiten, y ninguno va a logs ni a `audit_log`.
"""

import datetime
import uuid
from collections.abc import Callable
from typing import Annotated, Any

import platform_admin as pa
from fastapi import APIRouter, Depends, Response
from platform_admin import ClienteAuthAdmin
from platform_auth import SesionAdmin
from pydantic import BaseModel, ConfigDict, Field, field_validator
from turnos_dominio.settings_tenant import validar_settings

from admin_backend.dependencias import cliente_auth_admin_de, pool_admin, sesion_admin


class AltaTenantIn(BaseModel):
    # extra="ignore": status/plan/etc. del body no llegan al dominio (el alta siempre nace 'active' y 'pilot').
    model_config = ConfigDict(extra="ignore")

    name: str = Field(max_length=200)
    slug: str = Field(max_length=100)
    # Mismas reglas que el PATCH de settings del dashboard (`turnos_dominio.settings_tenant`): `platform_admin` no puede
    # importar `apps/shared`, así que se validan acá, antes de llamarlo (422 de validación, sin tocar la base).
    settings: dict[str, Any] | None = None
    owner_email: str = Field(max_length=254)
    # `Any` a propósito: la validación (obligatorio, 1 a 120 caracteres) la hace `platform_admin` y responde 422
    # sin repetir el valor (la validación de pydantic lo devolvería en el error).
    owner_nombre: Any = None

    @field_validator("settings")
    @classmethod
    def _settings_validos(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is not None:
            validar_settings(v)
        return v


class ReenviarInvitacionIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    owner_email: str | None = Field(default=None, max_length=254)
    owner_nombre: Any = None


class SettingIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    valor: Any
    # Debe ser true para bajar un valor vigente (p. ej. acortar el período de gracia); sin él, 409.
    confirmar_reduccion: bool = False


class FacturacionIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    paid_until: datetime.date | None = None
    billing_notes: str | None = None


router = APIRouter(tags=["panel"], dependencies=[Depends(sesion_admin)])

Admin = Annotated[SesionAdmin, Depends(sesion_admin)]
Pool = Annotated[Callable[[], Any], Depends(pool_admin)]
ClienteAuth = Annotated[ClienteAuthAdmin, Depends(cliente_auth_admin_de)]


@router.get("/tenants")
async def listar(admin: Admin, obtener_pool: Pool, status: str | None = None) -> list[pa.TenantAdmin]:
    return await pa.listar_tenants(obtener_pool(), actor_id=admin.user_id, status=status)


@router.post("/tenants", status_code=201)
async def alta(
    datos: AltaTenantIn, admin: Admin, obtener_pool: Pool, cliente_auth_admin: ClienteAuth
) -> dict[str, str]:
    tenant_data: dict[str, Any] = {"name": datos.name, "slug": datos.slug}
    if datos.settings is not None:
        tenant_data["settings"] = datos.settings
    res = await pa.create_tenant(
        obtener_pool(),
        cliente_auth_admin,
        tenant_data,
        datos.owner_email,
        owner_nombre=datos.owner_nombre,
        actor_id=admin.user_id,
    )
    return {"tenant_id": str(res.tenant_id), "estado_invitacion": res.estado}


@router.get("/tenants/{tenant_id}")
async def ficha(tenant_id: uuid.UUID, admin: Admin, obtener_pool: Pool) -> pa.TenantAdmin:
    return await pa.obtener_tenant(obtener_pool(), tenant_id, actor_id=admin.user_id)


@router.post("/tenants/{tenant_id}/suspender", status_code=204)
async def suspender(tenant_id: uuid.UUID, admin: Admin, obtener_pool: Pool) -> Response:
    await pa.suspender(obtener_pool(), tenant_id, actor_id=admin.user_id)
    return Response(status_code=204)


@router.post("/tenants/{tenant_id}/levantar-suspension", status_code=204)
async def levantar_suspension(tenant_id: uuid.UUID, admin: Admin, obtener_pool: Pool) -> Response:
    await pa.levantar_suspension(obtener_pool(), tenant_id, actor_id=admin.user_id)
    return Response(status_code=204)


@router.post("/tenants/{tenant_id}/dar-de-baja", status_code=204)
async def dar_de_baja(tenant_id: uuid.UUID, admin: Admin, obtener_pool: Pool) -> Response:
    await pa.dar_de_baja(obtener_pool(), tenant_id, actor_id=admin.user_id)
    return Response(status_code=204)


@router.post("/tenants/{tenant_id}/reactivar", status_code=204)
async def reactivar(tenant_id: uuid.UUID, admin: Admin, obtener_pool: Pool) -> Response:
    await pa.reactivar(obtener_pool(), tenant_id, actor_id=admin.user_id)
    return Response(status_code=204)


@router.post("/tenants/{tenant_id}/reenviar-invitacion")
async def reenviar(
    tenant_id: uuid.UUID,
    admin: Admin,
    obtener_pool: Pool,
    cliente_auth_admin: ClienteAuth,
    datos: ReenviarInvitacionIn | None = None,
) -> dict[str, str]:
    email = datos.owner_email if datos is not None else None
    nombre = datos.owner_nombre if datos is not None else None
    res = await pa.reenviar_invitacion(
        obtener_pool(), cliente_auth_admin, tenant_id, email, owner_nombre=nombre, actor_id=admin.user_id
    )
    return {"tenant_id": str(res.tenant_id), "estado_invitacion": res.estado}


@router.get("/tenants/{tenant_id}/facturacion")
async def leer_facturacion(tenant_id: uuid.UUID, admin: Admin, obtener_pool: Pool) -> pa.Facturacion:
    return await pa.obtener_facturacion(obtener_pool(), tenant_id, actor_id=admin.user_id)


@router.put("/tenants/{tenant_id}/facturacion")
async def editar_facturacion(
    tenant_id: uuid.UUID, datos: FacturacionIn, admin: Admin, obtener_pool: Pool
) -> pa.Facturacion:
    # Solo lo que vino en el body se modifica; `null` borra el valor.
    campos = {c: getattr(datos, c) for c in datos.model_fields_set & {"paid_until", "billing_notes"}}
    if not campos:
        raise pa.DatosInvalidos("hay que indicar paid_until y/o billing_notes")
    return await pa.editar_facturacion(obtener_pool(), tenant_id, actor_id=admin.user_id, **campos)


@router.get("/settings")
async def leer_settings(admin: Admin, obtener_pool: Pool) -> dict[str, Any]:
    return await pa.listar_settings(obtener_pool(), actor_id=admin.user_id)


@router.put("/settings/{clave}")
async def editar_setting(clave: str, datos: SettingIn, admin: Admin, obtener_pool: Pool) -> dict[str, Any]:
    return await pa.editar_setting(
        obtener_pool(), clave, datos.valor, actor_id=admin.user_id, confirmar_reduccion=datos.confirmar_reduccion
    )
