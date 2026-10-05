"""Catálogo del local: servicios, profesionales y qué servicios hace cada profesional (ARCHITECTURE.md §7.1).

Permisos (§2.4; el owner vivo siempre los tiene). Las lecturas las puede hacer cualquier staff activo del tenant:

  GET    /servicios, /servicios/{id}                         cualquier staff
  POST   /servicios                                          edit_prices
  PATCH  /servicios/{id}                                     edit_prices
  DELETE /servicios/{id}                                     edit_prices
  GET    /profesionales, /profesionales/{id}                 cualquier staff
  POST   /profesionales                                      manage_staff
  PATCH  /profesionales/{id}                                 manage_staff (is_active=false dispara en la base el
                                                             borrado del secret de Calendar del profesional)
  DELETE /profesionales/{id}                                 manage_staff (409 si tiene turnos u otros datos)
  GET    /profesionales/{id}/servicios                       cualquier staff
  PUT    /profesionales/{id}/servicios/{service_id}          manage_staff; `confirmation_mode` auto|approval
                                                             (`deposit` se rechaza con 422 en el piloto)
  DELETE /profesionales/{id}/servicios/{service_id}          manage_staff

Servicios exige `edit_prices` para todo lo que escribe, no solo para el precio: un servicio es nombre, duración y
precio juntos, y separar los permisos por campo no aporta nada que el piloto necesite.

Todo corre dentro de `tenant_tx` con el tenant de la sesión; cada query filtra además `tenant_id = $1` (la RLS es el
límite, no la única capa). Lo ajeno o inexistente es 404, nunca 403: no se revela qué existe en otro tenant.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from turnos_queries import listar_profesionales, listar_servicios

from dashboard_backend.dependencias import DependenciasDashboard, Transaccion
from dashboard_backend.errores_db import traducir_errores_db
from dashboard_backend.escritura_sql import armar_set, no_encontrado
from dashboard_backend.esquemas_catalogo import (
    ProfesionalIn,
    ProfesionalParche,
    ServicioDeProfesionalIn,
    ServicioIn,
    ServicioParche,
)

COLUMNAS_SERVICIO = "id, tenant_id, name, duration_minutes, price, deposit_amount, is_active, created_at"
COLUMNAS_PROFESIONAL = "id, tenant_id, name, is_active, tenant_user_id, created_at"
COLUMNAS_PS = "professional_id, service_id, confirmation_mode"

_SQL_SERVICIO = f"select {COLUMNAS_SERVICIO} from public.services where tenant_id = $1 and id = $2"
_SQL_PROFESIONAL = f"select {COLUMNAS_PROFESIONAL} from public.professionals where tenant_id = $1 and id = $2"
_SQL_ALTA_SERVICIO = f"""
insert into public.services (tenant_id, name, duration_minutes, price, deposit_amount, is_active)
values ($1, $2, $3, $4, $5, $6)
returning {COLUMNAS_SERVICIO}
"""
_SQL_ALTA_PROFESIONAL = f"""
insert into public.professionals (tenant_id, name, tenant_user_id, is_active)
values ($1, $2, $3, $4)
returning {COLUMNAS_PROFESIONAL}
"""
_SQL_LISTAR_PS = f"""
select {COLUMNAS_PS} from public.professional_services
where tenant_id = $1 and professional_id = $2
order by service_id
"""
_SQL_UPSERT_PS = f"""
insert into public.professional_services (tenant_id, professional_id, service_id, confirmation_mode)
values ($1, $2, $3, $4)
on conflict (professional_id, service_id) do update set confirmation_mode = excluded.confirmation_mode
returning {COLUMNAS_PS}
"""
_SQL_BORRAR_PS = """
delete from public.professional_services
where tenant_id = $1 and professional_id = $2 and service_id = $3
returning service_id
"""


def crear_router_catalogo(deps: DependenciasDashboard) -> APIRouter:
    router = APIRouter(tags=["catalogo"])
    lectura = deps.tx
    edita_precios = deps.tx_con_permiso("edit_prices")
    gestiona_personal = deps.tx_con_permiso("manage_staff")

    Lectura = Annotated[Transaccion, Depends(lectura)]
    EditaPrecios = Annotated[Transaccion, Depends(edita_precios)]
    GestionaPersonal = Annotated[Transaccion, Depends(gestiona_personal)]
    Limite = Annotated[int, Query(ge=1, le=200)]
    Offset = Annotated[int, Query(ge=0)]

    # --- servicios ----------------------------------------------------------------------------------------------

    @router.get("/servicios")
    async def listar_los_servicios(
        tx: Lectura, incluir_inactivos: bool = False, limite: Limite = 100, offset: Offset = 0
    ):
        return await listar_servicios(
            tx.conn,
            tenant_id=tx.sesion.tenant_id,
            incluir_inactivos=incluir_inactivos,
            limite=limite,
            offset=offset,
        )

    @router.get("/servicios/{servicio_id}")
    async def ver_servicio(servicio_id: uuid.UUID, tx: Lectura):
        fila = await tx.conn.fetchrow(_SQL_SERVICIO, tx.sesion.tenant_id, servicio_id)
        if fila is None:
            raise no_encontrado("El servicio")
        return dict(fila)

    @router.post("/servicios", status_code=201)
    async def crear_servicio(datos: ServicioIn, tx: EditaPrecios):
        with traducir_errores_db():
            fila = await tx.conn.fetchrow(
                _SQL_ALTA_SERVICIO,
                tx.sesion.tenant_id,
                datos.name,
                datos.duration_minutes,
                datos.price,
                datos.deposit_amount,
                datos.is_active,
            )
        return dict(fila)

    @router.patch("/servicios/{servicio_id}")
    async def modificar_servicio(servicio_id: uuid.UUID, datos: ServicioParche, tx: EditaPrecios):
        asignaciones, valores = armar_set(datos.cambios(), primer_parametro=3)
        sql = (
            f"update public.services set {asignaciones} where tenant_id = $1 and id = $2 returning {COLUMNAS_SERVICIO}"
        )
        with traducir_errores_db():
            fila = await tx.conn.fetchrow(sql, tx.sesion.tenant_id, servicio_id, *valores)
        if fila is None:
            raise no_encontrado("El servicio")
        return dict(fila)

    @router.delete("/servicios/{servicio_id}", status_code=204, response_class=Response)
    async def borrar_servicio(servicio_id: uuid.UUID, tx: EditaPrecios):
        with traducir_errores_db(en_borrado=True):
            fila = await tx.conn.fetchrow(
                "delete from public.services where tenant_id = $1 and id = $2 returning id",
                tx.sesion.tenant_id,
                servicio_id,
            )
        if fila is None:
            raise no_encontrado("El servicio")

    # --- profesionales ------------------------------------------------------------------------------------------

    @router.get("/profesionales")
    async def listar_los_profesionales(
        tx: Lectura, incluir_inactivos: bool = False, limite: Limite = 100, offset: Offset = 0
    ):
        return await listar_profesionales(
            tx.conn,
            tenant_id=tx.sesion.tenant_id,
            incluir_inactivos=incluir_inactivos,
            limite=limite,
            offset=offset,
        )

    @router.get("/profesionales/{profesional_id}")
    async def ver_profesional(profesional_id: uuid.UUID, tx: Lectura):
        fila = await tx.conn.fetchrow(_SQL_PROFESIONAL, tx.sesion.tenant_id, profesional_id)
        if fila is None:
            raise no_encontrado("El profesional")
        return dict(fila)

    @router.post("/profesionales", status_code=201)
    async def crear_profesional(datos: ProfesionalIn, tx: GestionaPersonal):
        with traducir_errores_db():
            fila = await tx.conn.fetchrow(
                _SQL_ALTA_PROFESIONAL, tx.sesion.tenant_id, datos.name, datos.tenant_user_id, datos.is_active
            )
        return dict(fila)

    @router.patch("/profesionales/{profesional_id}")
    async def modificar_profesional(profesional_id: uuid.UUID, datos: ProfesionalParche, tx: GestionaPersonal):
        asignaciones, valores = armar_set(datos.cambios(), primer_parametro=3)
        sql = (
            f"update public.professionals set {asignaciones} where tenant_id = $1 and id = $2 "
            f"returning {COLUMNAS_PROFESIONAL}"
        )
        with traducir_errores_db():
            fila = await tx.conn.fetchrow(sql, tx.sesion.tenant_id, profesional_id, *valores)
        if fila is None:
            raise no_encontrado("El profesional")
        return dict(fila)

    @router.delete("/profesionales/{profesional_id}", status_code=204, response_class=Response)
    async def borrar_profesional(profesional_id: uuid.UUID, tx: GestionaPersonal):
        with traducir_errores_db(en_borrado=True):
            fila = await tx.conn.fetchrow(
                "delete from public.professionals where tenant_id = $1 and id = $2 returning id",
                tx.sesion.tenant_id,
                profesional_id,
            )
        if fila is None:
            raise no_encontrado("El profesional")

    # --- professional_services ----------------------------------------------------------------------------------

    async def _existe_profesional(tx: Transaccion, profesional_id: uuid.UUID) -> None:
        if (
            await tx.conn.fetchval(
                "select 1 from public.professionals where tenant_id = $1 and id = $2",
                tx.sesion.tenant_id,
                profesional_id,
            )
            is None
        ):
            raise no_encontrado("El profesional")

    @router.get("/profesionales/{profesional_id}/servicios")
    async def servicios_del_profesional(profesional_id: uuid.UUID, tx: Lectura):
        await _existe_profesional(tx, profesional_id)
        return [dict(f) for f in await tx.conn.fetch(_SQL_LISTAR_PS, tx.sesion.tenant_id, profesional_id)]

    @router.put("/profesionales/{profesional_id}/servicios/{servicio_id}")
    async def asignar_servicio(
        profesional_id: uuid.UUID, servicio_id: uuid.UUID, datos: ServicioDeProfesionalIn, tx: GestionaPersonal
    ):
        await _existe_profesional(tx, profesional_id)
        if (
            await tx.conn.fetchval(
                "select 1 from public.services where tenant_id = $1 and id = $2", tx.sesion.tenant_id, servicio_id
            )
            is None
        ):
            raise no_encontrado("El servicio")
        with traducir_errores_db():
            fila = await tx.conn.fetchrow(
                _SQL_UPSERT_PS, tx.sesion.tenant_id, profesional_id, servicio_id, datos.confirmation_mode
            )
        return dict(fila)

    @router.delete("/profesionales/{profesional_id}/servicios/{servicio_id}", status_code=204, response_class=Response)
    async def quitar_servicio(profesional_id: uuid.UUID, servicio_id: uuid.UUID, tx: GestionaPersonal):
        with traducir_errores_db(en_borrado=True):
            fila = await tx.conn.fetchrow(_SQL_BORRAR_PS, tx.sesion.tenant_id, profesional_id, servicio_id)
        if fila is None:
            raise no_encontrado("La asignación del servicio al profesional")

    return router
