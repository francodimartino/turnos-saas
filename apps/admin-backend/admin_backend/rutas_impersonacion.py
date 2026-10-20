"""Impersonación de SOLO LECTURA: Franco ve lo que ve el dashboard de un tenant (ARCHITECTURE.md §2.11.4, §3.2).

  GET /tenants/{id}/impersonar/bookings?desde=&hasta=&estados=&professional_id=&limite=&offset=
  GET /tenants/{id}/impersonar/bookings/{booking_id}
  GET /tenants/{id}/impersonar/clientes?busqueda=
  GET /tenants/{id}/impersonar/servicios?incluir_inactivos=
  GET /tenants/{id}/impersonar/profesionales?incluir_inactivos=
  GET /tenants/{id}/impersonar/packs?end_customer_id=

Garantías:
  - Reusa las funciones de `turnos_queries` (las mismas del dashboard), siempre con el `tenant_id` de la ruta de
    forma explícita (segunda barrera): `readonly_tx` fija ese tenant y la RLS ya no tiene bypass para el rol.
  - Las lecturas corren con el pool del rol `platform_admin_readonly` dentro de `readonly_tx` (`BEGIN READ ONLY`):
    la base rechaza cualquier escritura (el rol solo tiene SELECT y la transacción es de solo lectura).
  - Cada vista deja una fila de `audit_log` ANTES de leer, escrita con el rol `platform_admin`
    (`actor_type='platform_admin'`, `action='impersonate_view'`, metadata con la vista, sin datos del tenant). La
    policy RESTRICTIVE de `audit_log` esconde esas filas al `dashboard_service`: el tenant nunca las ve.
  - Solo hay rutas GET: no existe ninguna ruta de escritura de impersonación.

Router de módulo (`router`, registrado con `include_router`): la sesión y los dos pools (`platform_admin` para el audit,
`platform_admin_readonly` para leer) se toman con `Depends` de `app.state` (`dependencias`); `_vista` los recibe.
"""

import datetime
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any

import turnos_queries as tq
from fastapi import APIRouter, Depends, HTTPException, Query
from platform_admin import TenantNoEncontrado
from platform_audit import registrar
from platform_auth import SesionAdmin
from platform_tenancy import admin_tx, readonly_tx

from admin_backend.dependencias import pool_admin, pool_readonly, sesion_admin

ACCION_AUDIT = "impersonate_view"
LIMITE_MAXIMO = 500


@dataclass(frozen=True)
class Pools:
    """Getters de los dos pools: `admin` (rol `platform_admin`, para el audit) y `readonly` (solo lectura)."""

    admin: Callable[[], Any]
    readonly: Callable[[], Any]


def pools_de_impersonacion(
    admin: Annotated[Callable[[], Any], Depends(pool_admin)],
    readonly: Annotated[Callable[[], Any], Depends(pool_readonly)],
) -> Pools:
    return Pools(admin, readonly)


router = APIRouter(
    tags=["impersonacion"], prefix="/tenants/{tenant_id}/impersonar", dependencies=[Depends(sesion_admin)]
)

Admin = Annotated[SesionAdmin, Depends(sesion_admin)]
PoolsDeps = Annotated[Pools, Depends(pools_de_impersonacion)]
Limite = Annotated[int, Query(ge=1, le=LIMITE_MAXIMO)]
Offset = Annotated[int, Query(ge=0, le=2_147_483_647)]


async def _vista(
    pools: Pools,
    tenant_id: uuid.UUID,
    admin: SesionAdmin,
    metadata: dict[str, Any],
    consulta: Callable[[Any], Awaitable[Any]],
) -> Any:
    # 1) Audit con el rol platform_admin (confirma antes que el tenant existe). Queda aunque la lectura falle.
    async with admin_tx(pools.admin(), admin.user_id) as conn:
        if await conn.fetchval("select 1 from public.tenants where id = $1", tenant_id) is None:
            raise TenantNoEncontrado("El tenant no existe")
        await registrar(conn, ACCION_AUDIT, "tenants", tenant_id, metadata, tenant_id=tenant_id)
    # 2) Lectura con el rol de solo lectura.
    async with readonly_tx(pools.readonly(), tenant_id, admin.user_id) as conn:
        return await consulta(conn)


@router.get("/bookings")
async def bookings(
    tenant_id: uuid.UUID,
    admin: Admin,
    pools: PoolsDeps,
    desde: datetime.datetime,
    hasta: datetime.datetime,
    estados: Annotated[list[str] | None, Query()] = None,
    professional_id: uuid.UUID | None = None,
    limite: Limite = tq.consultas.LIMITE_POR_DEFECTO,
    offset: Offset = 0,
) -> list[dict[str, Any]]:
    return await _vista(
        pools,
        tenant_id,
        admin,
        {"vista": "bookings"},
        lambda conn: tq.listar_bookings(
            conn,
            tenant_id=tenant_id,
            desde=desde,
            hasta=hasta,
            estados=estados,
            professional_id=professional_id,
            limite=limite,
            offset=offset,
        ),
    )


@router.get("/bookings/{booking_id}")
async def booking(tenant_id: uuid.UUID, booking_id: uuid.UUID, admin: Admin, pools: PoolsDeps) -> dict[str, Any]:
    detalle = await _vista(
        pools,
        tenant_id,
        admin,
        {"vista": "booking", "booking_id": str(booking_id)},
        lambda conn: tq.detalle_booking(conn, tenant_id=tenant_id, booking_id=booking_id),
    )
    if detalle is None:
        raise HTTPException(
            status_code=404, detail={"codigo": "booking_no_encontrado", "mensaje": "El turno no existe"}
        )
    return detalle


@router.get("/clientes")
async def clientes(
    tenant_id: uuid.UUID,
    admin: Admin,
    pools: PoolsDeps,
    busqueda: Annotated[str | None, Query(max_length=100)] = None,
    limite: Limite = tq.consultas.LIMITE_POR_DEFECTO,
    offset: Offset = 0,
) -> list[dict[str, Any]]:
    return await _vista(
        pools,
        tenant_id,
        admin,
        {"vista": "clientes"},
        lambda conn: tq.listar_clientes(conn, tenant_id=tenant_id, busqueda=busqueda, limite=limite, offset=offset),
    )


@router.get("/servicios")
async def servicios(
    tenant_id: uuid.UUID,
    admin: Admin,
    pools: PoolsDeps,
    incluir_inactivos: bool = False,
    limite: Limite = tq.consultas.LIMITE_POR_DEFECTO,
    offset: Offset = 0,
) -> list[dict[str, Any]]:
    return await _vista(
        pools,
        tenant_id,
        admin,
        {"vista": "servicios"},
        lambda conn: tq.listar_servicios(
            conn, tenant_id=tenant_id, incluir_inactivos=incluir_inactivos, limite=limite, offset=offset
        ),
    )


@router.get("/profesionales")
async def profesionales(
    tenant_id: uuid.UUID,
    admin: Admin,
    pools: PoolsDeps,
    incluir_inactivos: bool = False,
    limite: Limite = tq.consultas.LIMITE_POR_DEFECTO,
    offset: Offset = 0,
) -> list[dict[str, Any]]:
    return await _vista(
        pools,
        tenant_id,
        admin,
        {"vista": "profesionales"},
        lambda conn: tq.listar_profesionales(
            conn, tenant_id=tenant_id, incluir_inactivos=incluir_inactivos, limite=limite, offset=offset
        ),
    )


@router.get("/packs")
async def packs(
    tenant_id: uuid.UUID,
    admin: Admin,
    pools: PoolsDeps,
    end_customer_id: uuid.UUID | None = None,
    limite: Limite = tq.consultas.LIMITE_POR_DEFECTO,
    offset: Offset = 0,
) -> list[dict[str, Any]]:
    return await _vista(
        pools,
        tenant_id,
        admin,
        {"vista": "packs"},
        lambda conn: tq.listar_packs(
            conn, tenant_id=tenant_id, end_customer_id=end_customer_id, limite=limite, offset=offset
        ),
    )
