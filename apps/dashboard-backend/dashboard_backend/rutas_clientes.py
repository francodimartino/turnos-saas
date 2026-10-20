"""Endpoints `/clientes`: gestión de `end_customers` y derecho al olvido (ARCHITECTURE.md §2.3, §2.4, §2.8, §3.5).

Permisos (§2.4), chequeados en vivo dentro de la misma transacción del endpoint (el owner vivo los tiene todos).
Las claves existen en el catálogo `permission_key` sembrado en la base; ver los clientes reutiliza `view_bookings`
(el catálogo no tiene una clave propia para ver clientes), y gestionar y anonimizar tienen clave propia:

  GET   /clientes, GET /clientes/{id}      `view_bookings`     (los clientes se ven junto a las reservas)
  POST  /clientes, PATCH /clientes/{id}    `manage_customers`  (alta y corrección de datos)
  POST  /clientes/{id}/anonimizar          `delete_customers`  (acto irreversible: anonimizar)

No hay ruta DELETE: `dashboard_service` no puede borrar `end_customers` (§3.2); borrar es anonimizar.

Este módulo solo traduce HTTP: las reglas, el SQL y la auditoría están en `gestion_clientes` y `clientes`, que no
conocen FastAPI. Listado, detalle, alta y edición usan una única transacción (la dependencia
`transaccion_con_permiso`) para la sesión viva, el permiso y la escritura.

Anonimizar también abre una sola transacción para la sesión viva, el permiso y la anonimización, y hace COMMIT al
salir de ella. Recién después, a propósito fuera de esa transacción, `limpiar_calendario` usa transacciones cortas
propias para limpiar Google Calendar sin ninguna conexión tomada mientras se espera a la red externa. Una falla del
calendario nunca deshace ni oculta un borrado ya confirmado. Esa segunda etapa es la única excepción a "una
transacción por request". La fábrica de Calendar sale de `app.state.calendario` con `Depends`.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from platform_auth import IdentidadJWT, Permiso
from turnos_http import error_http
from turnos_observabilidad import contexto_log, log_evento_obligatorio
from turnos_telefonos import TelefonoInvalido

from dashboard_backend import clientes as c
from dashboard_backend import gestion_clientes as g
from dashboard_backend.dependencias import (
    ACTOR_TYPE,
    Transaccion,
    dependencias_de,
    identidad_actual,
    pool_actual,
    transaccion_con_permiso,
)
from dashboard_backend.esquemas_clientes import AltaCliente, EdicionCliente
from dashboard_backend.respuestas_clientes import AnonimizacionOut, ClienteOut, ListaClientesOut

PERMISO_VER = Permiso.VER_TURNOS
PERMISO_GESTIONAR = Permiso.GESTIONAR_CLIENTES
PERMISO_ANONIMIZAR = Permiso.ELIMINAR_CLIENTES

router = APIRouter(prefix="/clientes", tags=["clientes"])

# Con nombre para poder reemplazarlas en `app.dependency_overrides`.
tx_ver = transaccion_con_permiso(PERMISO_VER)
tx_gestionar = transaccion_con_permiso(PERMISO_GESTIONAR)

Ver = Annotated[Transaccion, Depends(tx_ver)]
Gestionar = Annotated[Transaccion, Depends(tx_gestionar)]

_ERRORES = (TelefonoInvalido, c.ErrorExtraData, g.ErrorCliente)


def fabrica_calendario(request: Request) -> Any:
    """Fábrica de servicios de Calendar de la app (`app.state.calendario`)."""
    return request.app.state.calendario


def _traducir(e: Exception) -> HTTPException:
    """Errores de dominio de la gestión de clientes -> respuesta HTTP."""
    if isinstance(e, TelefonoInvalido):
        return error_http(422, "telefono_invalido", str(e))
    if isinstance(e, c.ErrorExtraData):
        return error_http(422, "extra_data_invalido", str(e))
    if isinstance(e, g.ErrorNombreInvalido):
        return error_http(422, "nombre_invalido", "El nombre es obligatorio")
    if isinstance(e, g.ErrorCampoNulo):
        return error_http(422, "campo_nulo", "phone y name no pueden ser nulos")
    if isinstance(e, g.ErrorClienteInexistente):
        return error_http(404, "cliente_no_encontrado", "Cliente no encontrado")
    if isinstance(e, g.ErrorClienteAnonimizado):
        return error_http(409, "cliente_anonimizado", "El cliente fue anonimizado y no se puede editar")
    if isinstance(e, g.ErrorClienteDuplicado):
        return error_http(
            409, "cliente_duplicado", "Ya existe un cliente con ese teléfono", cliente_id=str(e.cliente_id)
        )
    return error_http(500, "error_interno", "Error interno")


@router.get("", response_model=ListaClientesOut)
async def listar(
    tx: Ver,
    busqueda: Annotated[str | None, Query(max_length=100)] = None,
    limite: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=2_147_483_647)] = 0,
) -> Any:
    return await g.listar(tx.conn, tx.sesion.tenant_id, busqueda, limite, offset)


@router.get("/{cliente_id}", response_model=ClienteOut)
async def detalle(cliente_id: uuid.UUID, tx: Ver) -> Any:
    try:
        return await g.detalle(tx.conn, tx.sesion.tenant_id, cliente_id)
    except _ERRORES as e:
        raise _traducir(e) from None


@router.post("", status_code=201, response_model=ClienteOut)
async def alta(datos: AltaCliente, tx: Gestionar) -> Any:
    try:
        return await g.crear(
            tx.conn, tx.sesion.tenant_id, datos.phone, datos.name, datos.extra_data, set(datos.model_fields_set)
        )
    except _ERRORES as e:
        raise _traducir(e) from None


@router.patch("/{cliente_id}", response_model=ClienteOut)
async def editar(cliente_id: uuid.UUID, datos: EdicionCliente, tx: Gestionar) -> Any:
    try:
        return await g.editar(
            tx.conn,
            tx.sesion.tenant_id,
            cliente_id,
            datos.phone,
            datos.name,
            datos.extra_data,
            set(datos.model_fields_set),
        )
    except _ERRORES as e:
        raise _traducir(e) from None


@router.post("/{cliente_id}/anonimizar", response_model=AnonimizacionOut)
async def anonimizar(
    cliente_id: uuid.UUID,
    request: Request,
    identidad: Annotated[IdentidadJWT, Depends(identidad_actual)],
    pool: Annotated[Any, Depends(pool_actual)],
    fabrica: Annotated[Any, Depends(fabrica_calendario)],
) -> Any:
    # Una sola transacción: sesión viva + permiso + anonimización. Salir del `async with` sin error = COMMIT hecho.
    # No se usa la dependencia `transaccion_con_permiso` porque esa se cierra recién al terminar el endpoint, y la
    # limpieza de Calendar tiene que correr con el borrado ya confirmado.
    try:
        async with dependencias_de(request).abrir_con_permiso(identidad, PERMISO_ANONIMIZAR) as tx:
            sesion = tx.sesion
            await g.anonimizar(tx.conn, sesion.tenant_id, cliente_id)
    except _ERRORES as e:
        raise _traducir(e) from None
    with contexto_log(tenant_id=str(sesion.tenant_id), actor_type=ACTOR_TYPE):
        calendario = await g.limpiar_calendario(pool, sesion, cliente_id, fabrica)
        # `tenant_id` y `actor_type` ya viajan en el contexto de logs; acá solo ids opacos y conteos.
        log_evento_obligatorio(
            "anonymize_end_customer",
            end_customer_id=str(cliente_id),
            calendar_events=calendario["eventos"],
            calendar_not_scrubbed=calendario["no_limpiados"],
        )
    return {"id": str(cliente_id), "sin_cliente_identificable": True, "calendario": calendario}
