"""Disponibilidad: reglas semanales por profesional y bloqueos puntuales (ARCHITECTURE.md §7.1).

Permisos (§2.4; el owner vivo siempre los tiene). Las lecturas las puede hacer cualquier staff activo del tenant:

  GET    /reglas-disponibilidad[?professional_id=], /reglas-disponibilidad/{id}   cualquier staff
  POST   /reglas-disponibilidad                                                   manage_schedule
  PATCH  /reglas-disponibilidad/{id}                                              manage_schedule
  DELETE /reglas-disponibilidad/{id}                                              manage_schedule
  GET    /bloqueos-disponibilidad[?professional_id=&desde=&hasta=], .../{id}      cualquier staff
  POST   /bloqueos-disponibilidad                                                 manage_schedule
  PATCH  /bloqueos-disponibilidad/{id}                                            manage_schedule
  DELETE /bloqueos-disponibilidad/{id}                                            manage_schedule

Reglas: `day_of_week` 0 = domingo ... 6 = sábado; `start_time`/`end_time` son hora local del local (sin zona).
Bloqueos: `start_at`/`end_at` con zona horaria; el rango es [inicio, fin). Con `professional_id` null el bloqueo es de
todo el local. Un profesional de otro tenant (o inexistente) es 422 `referencia_invalida`; una regla o bloqueo de otro
tenant es 404. Ventana invertida, día fuera de 0-6 y rango vacío los rechazan los `check` de la base (422 con mensaje).
"""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from dashboard_backend.dependencias import DependenciasDashboard, Transaccion
from dashboard_backend.errores_db import traducir_errores_db
from dashboard_backend.escritura_sql import armar_set, no_encontrado
from dashboard_backend.esquemas_catalogo import BloqueoIn, BloqueoParche, ReglaIn, ReglaParche

COLUMNAS_REGLA = "id, tenant_id, professional_id, day_of_week, start_time, end_time, created_at"
COLUMNAS_BLOQUEO = (
    "id, tenant_id, professional_id, lower(time_range) as start_at, upper(time_range) as end_at, reason, created_at"
)

_SQL_LISTAR_REGLAS = f"""
select {COLUMNAS_REGLA} from public.availability_rules
where tenant_id = $1 and ($2::uuid is null or professional_id = $2::uuid)
order by professional_id, day_of_week, start_time, id
limit $3 offset $4
"""
_SQL_VER_REGLA = f"select {COLUMNAS_REGLA} from public.availability_rules where tenant_id = $1 and id = $2"
_SQL_ALTA_REGLA = f"""
insert into public.availability_rules (tenant_id, professional_id, day_of_week, start_time, end_time)
values ($1, $2, $3, $4, $5)
returning {COLUMNAS_REGLA}
"""
_SQL_LISTAR_BLOQUEOS = f"""
select {COLUMNAS_BLOQUEO} from public.availability_blocks
where tenant_id = $1
  and ($2::uuid is null or professional_id = $2::uuid)
  and ($3::timestamptz is null or upper(time_range) > $3::timestamptz)
  and ($4::timestamptz is null or lower(time_range) < $4::timestamptz)
order by lower(time_range), id
limit $5 offset $6
"""
_SQL_VER_BLOQUEO = f"select {COLUMNAS_BLOQUEO} from public.availability_blocks where tenant_id = $1 and id = $2"
_SQL_ALTA_BLOQUEO = f"""
insert into public.availability_blocks (tenant_id, professional_id, time_range, reason)
values ($1, $2, tstzrange($3::timestamptz, $4::timestamptz, '[)'), $5)
returning {COLUMNAS_BLOQUEO}
"""


def crear_router_disponibilidad(deps: DependenciasDashboard) -> APIRouter:
    router = APIRouter(tags=["disponibilidad"])
    Lectura = Annotated[Transaccion, Depends(deps.tx)]
    GestionaAgenda = Annotated[Transaccion, Depends(deps.tx_con_permiso("manage_schedule"))]
    Limite = Annotated[int, Query(ge=1, le=500)]
    Offset = Annotated[int, Query(ge=0)]

    # --- reglas semanales ---------------------------------------------------------------------------------------

    @router.get("/reglas-disponibilidad")
    async def listar_reglas(
        tx: Lectura, professional_id: uuid.UUID | None = None, limite: Limite = 200, offset: Offset = 0
    ):
        filas = await tx.conn.fetch(_SQL_LISTAR_REGLAS, tx.sesion.tenant_id, professional_id, limite, offset)
        return [dict(f) for f in filas]

    @router.get("/reglas-disponibilidad/{regla_id}")
    async def ver_regla(regla_id: uuid.UUID, tx: Lectura):
        fila = await tx.conn.fetchrow(_SQL_VER_REGLA, tx.sesion.tenant_id, regla_id)
        if fila is None:
            raise no_encontrado("La regla de disponibilidad")
        return dict(fila)

    @router.post("/reglas-disponibilidad", status_code=201)
    async def crear_regla(datos: ReglaIn, tx: GestionaAgenda):
        with traducir_errores_db():
            fila = await tx.conn.fetchrow(
                _SQL_ALTA_REGLA,
                tx.sesion.tenant_id,
                datos.professional_id,
                datos.day_of_week,
                datos.start_time,
                datos.end_time,
            )
        return dict(fila)

    @router.patch("/reglas-disponibilidad/{regla_id}")
    async def modificar_regla(regla_id: uuid.UUID, datos: ReglaParche, tx: GestionaAgenda):
        asignaciones, valores = armar_set(datos.cambios(), primer_parametro=3)
        sql = (
            f"update public.availability_rules set {asignaciones} where tenant_id = $1 and id = $2 "
            f"returning {COLUMNAS_REGLA}"
        )
        with traducir_errores_db():
            fila = await tx.conn.fetchrow(sql, tx.sesion.tenant_id, regla_id, *valores)
        if fila is None:
            raise no_encontrado("La regla de disponibilidad")
        return dict(fila)

    @router.delete("/reglas-disponibilidad/{regla_id}", status_code=204, response_class=Response)
    async def borrar_regla(regla_id: uuid.UUID, tx: GestionaAgenda):
        with traducir_errores_db(en_borrado=True):
            fila = await tx.conn.fetchrow(
                "delete from public.availability_rules where tenant_id = $1 and id = $2 returning id",
                tx.sesion.tenant_id,
                regla_id,
            )
        if fila is None:
            raise no_encontrado("La regla de disponibilidad")

    # --- bloqueos -----------------------------------------------------------------------------------------------

    @router.get("/bloqueos-disponibilidad")
    async def listar_bloqueos(
        tx: Lectura,
        professional_id: uuid.UUID | None = None,
        desde: datetime | None = None,
        hasta: datetime | None = None,
        limite: Limite = 200,
        offset: Offset = 0,
    ):
        filas = await tx.conn.fetch(
            _SQL_LISTAR_BLOQUEOS, tx.sesion.tenant_id, professional_id, desde, hasta, limite, offset
        )
        return [dict(f) for f in filas]

    @router.get("/bloqueos-disponibilidad/{bloqueo_id}")
    async def ver_bloqueo(bloqueo_id: uuid.UUID, tx: Lectura):
        fila = await tx.conn.fetchrow(_SQL_VER_BLOQUEO, tx.sesion.tenant_id, bloqueo_id)
        if fila is None:
            raise no_encontrado("El bloqueo")
        return dict(fila)

    @router.post("/bloqueos-disponibilidad", status_code=201)
    async def crear_bloqueo(datos: BloqueoIn, tx: GestionaAgenda):
        with traducir_errores_db():
            fila = await tx.conn.fetchrow(
                _SQL_ALTA_BLOQUEO,
                tx.sesion.tenant_id,
                datos.professional_id,
                datos.start_at,
                datos.end_at,
                datos.reason,
            )
        return dict(fila)

    @router.patch("/bloqueos-disponibilidad/{bloqueo_id}")
    async def modificar_bloqueo(bloqueo_id: uuid.UUID, datos: BloqueoParche, tx: GestionaAgenda):
        cambios = datos.cambios()
        inicio, fin = cambios.pop("start_at", None), cambios.pop("end_at", None)
        asignaciones, valores = armar_set(cambios, primer_parametro=3)
        partes = [asignaciones] if asignaciones else []
        if inicio is not None or fin is not None:
            # El que no se envía conserva el valor actual del rango.
            n = 3 + len(valores)
            partes.append(
                f"time_range = tstzrange(coalesce(${n}::timestamptz, lower(time_range)), "
                f"coalesce(${n + 1}::timestamptz, upper(time_range)), '[)')"
            )
            valores += [inicio, fin]
        sql = (
            f"update public.availability_blocks set {', '.join(partes)} where tenant_id = $1 and id = $2 "
            f"returning {COLUMNAS_BLOQUEO}"
        )
        with traducir_errores_db():
            fila = await tx.conn.fetchrow(sql, tx.sesion.tenant_id, bloqueo_id, *valores)
        if fila is None:
            raise no_encontrado("El bloqueo")
        return dict(fila)

    @router.delete("/bloqueos-disponibilidad/{bloqueo_id}", status_code=204, response_class=Response)
    async def borrar_bloqueo(bloqueo_id: uuid.UUID, tx: GestionaAgenda):
        with traducir_errores_db(en_borrado=True):
            fila = await tx.conn.fetchrow(
                "delete from public.availability_blocks where tenant_id = $1 and id = $2 returning id",
                tx.sesion.tenant_id,
                bloqueo_id,
            )
        if fila is None:
            raise no_encontrado("El bloqueo")

    return router
