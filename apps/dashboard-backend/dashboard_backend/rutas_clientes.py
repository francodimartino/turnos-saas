"""Endpoints `/clientes`: gestión de `end_customers` y derecho al olvido (ARCHITECTURE.md §2.3, §2.4, §2.8, §3.5).

Permisos (§2.4), chequeados en vivo dentro de la misma transacción del endpoint (el owner vivo los tiene todos).
Las claves existen en el catálogo `permission_key` sembrado en la base; ver los clientes reutiliza `view_bookings`
(el catálogo no tiene una clave propia para ver clientes), y gestionar y anonimizar tienen clave propia:

  GET   /clientes, GET /clientes/{id}      `view_bookings`     (los clientes se ven junto a las reservas)
  POST  /clientes, PATCH /clientes/{id}    `manage_customers`  (alta y corrección de datos)
  POST  /clientes/{id}/anonimizar          `delete_customers`  (acto irreversible: anonimizar)

No hay ruta DELETE: `dashboard_service` no puede borrar `end_customers` (§3.2); borrar es anonimizar.

Anonimizar separa etapas a propósito. La primera ejecuta `anonymize_end_customer` y hace COMMIT; recién
después, una transacción corta obtiene `calendar_scrub_targets`, se limpian los eventos de Google Calendar sin ninguna
conexión tomada (red externa) y otra transacción corta registra `calendar_scrub_failed` por cada evento que no se
pudo limpiar. Una falla del calendario nunca deshace ni oculta un borrado ya confirmado.
"""

import json
import logging
import uuid
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from observabilidad import log_evento_obligatorio
from platform_audit import registrar
from platform_auth import ErrorAuth, SesionDashboard, verificar_permiso
from platform_calendar import ErrorConfiguracionCalendar, EventoNoLimpiado
from platform_tenancy import tenant_tx
from pydantic import BaseModel, ConfigDict, Field, field_validator
from telefonos import TelefonoInvalido, normalizar_e164
from turnos_queries import listar_clientes

from dashboard_backend import clientes as c
from dashboard_backend.calendario import ConexionBajoDemanda, FabricaCalendario
from dashboard_backend.dependencias import DependenciasDashboard, Transaccion, http_desde_error_auth
from dashboard_backend.valores_json import contiene_no_finito

PERMISO_VER = "view_bookings"
PERMISO_GESTIONAR = "manage_customers"
PERMISO_ANONIMIZAR = "delete_customers"

MOTIVO_SIN_INTEGRACION = "sin_integracion"
MOTIVO_CONFIGURACION = "configuracion_calendar"
MOTIVO_INESPERADO = "error_inesperado"

SQLSTATE_PRIVILEGIO_INSUFICIENTE = "42501"

_log = logging.getLogger("dashboard_backend.clientes")


def _sin_no_finitos(valor: dict[str, Any] | None) -> dict[str, Any] | None:
    """Infinity/NaN, aun anidados, no entran en jsonb: sin esto terminaban en un 500 al insertar."""
    if valor is not None and contiene_no_finito(valor):
        raise ValueError("extra_data no admite valores no finitos (Infinity, NaN)")
    return valor


class AltaCliente(BaseModel):
    # `extra="ignore"`: un `tenant_id` en el body se descarta; el tenant sale de la sesión.
    model_config = ConfigDict(extra="ignore")

    phone: str = Field(max_length=64)
    name: str = Field(max_length=200)
    extra_data: dict[str, Any] = Field(default_factory=dict)

    _finito = field_validator("extra_data")(_sin_no_finitos)


class EdicionCliente(BaseModel):
    model_config = ConfigDict(extra="ignore")

    phone: str | None = Field(default=None, max_length=64)
    name: str | None = Field(default=None, max_length=200)
    extra_data: dict[str, Any] | None = None

    _finito = field_validator("extra_data")(_sin_no_finitos)


def _error(estado: int, codigo: str, mensaje: str, **extra: Any) -> HTTPException:
    return HTTPException(status_code=estado, detail={"codigo": codigo, "mensaje": mensaje, **extra})


def _telefono(valor: str) -> str:
    try:
        return normalizar_e164(valor)
    except TelefonoInvalido as e:
        raise _error(422, "telefono_invalido", str(e)) from None


def _nombre(valor: str) -> str:
    nombre = valor.strip()
    if not nombre:
        raise _error(422, "nombre_invalido", "El nombre es obligatorio")
    return nombre


async def _extra_data(conn: Any, tenant_id: uuid.UUID, extra: dict[str, Any]) -> dict[str, Any]:
    if not extra:
        return {}
    settings = c.a_json(await conn.fetchval(c.SQL_SETTINGS, tenant_id)) or {}
    campos = settings.get(c.CAMPOS_EXTRA_SETTING) if isinstance(settings, dict) else None
    try:
        return c.validar_extra_data(extra, campos)
    except c.ErrorExtraData as e:
        raise _error(422, "extra_data_invalido", str(e)) from None


async def _sin_duplicado(conn: Any, tenant_id: uuid.UUID, telefono: str, excluir: uuid.UUID | None) -> None:
    await conn.fetchval(c.SQL_BLOQUEO_ALTA, tenant_id, telefono)
    existente = await conn.fetchval(c.SQL_DUPLICADO, tenant_id, telefono, excluir)
    if existente is not None:
        raise _error(409, "cliente_duplicado", "Ya existe un cliente con ese teléfono", cliente_id=str(existente))


async def _limpiar_calendario(
    pool: Any, sesion: SesionDashboard, cliente_id: uuid.UUID, fabrica: FabricaCalendario
) -> dict[str, Any]:
    """Segunda etapa del borrado: limpia Google Calendar y audita lo que no se pudo. Nunca lanza.

    Tres pasos, ninguno con una conexión tomada durante la espera a Google: (1) transacción corta que lee los eventos
    a limpiar, (2) llamadas a Google sin transacción (el servicio lee la base con transacciones cortas propias),
    (3) transacción corta que registra `calendar_scrub_failed` por cada evento no limpiado.
    """
    try:
        async with tenant_tx(pool, sesion.tenant_id, sesion.user_id) as conn:
            filas = await conn.fetch(c.SQL_TARGETS, sesion.tenant_id, cliente_id)
        pares = [(f["integration_id"], f["google_calendar_event_id"]) for f in filas if f["integration_id"] is not None]
        fallidos = [
            EventoNoLimpiado(None, f["google_calendar_event_id"], MOTIVO_SIN_INTEGRACION)  # type: ignore[arg-type]
            for f in filas
            if f["integration_id"] is None
        ]
        if pares:
            try:
                abrir_tx = lambda: tenant_tx(pool, sesion.tenant_id, sesion.user_id)  # noqa: E731
                async with fabrica(ConexionBajoDemanda(abrir_tx)) as servicio:
                    fallidos.extend(await servicio.limpiar_eventos(pares))
            except ErrorConfiguracionCalendar:
                fallidos.extend(EventoNoLimpiado(integ, evento, MOTIVO_CONFIGURACION) for integ, evento in pares)
            except Exception as exc:
                _log.error("calendar_scrub_inesperado", extra={"error": type(exc).__name__})
                fallidos.extend(EventoNoLimpiado(integ, evento, MOTIVO_INESPERADO) for integ, evento in pares)
        if fallidos:
            async with tenant_tx(pool, sesion.tenant_id, sesion.user_id) as conn:
                for f in fallidos:
                    # Metadata sin PII: solo el id del evento de Google y el motivo.
                    await registrar(
                        conn,
                        "calendar_scrub_failed",
                        "end_customers",
                        cliente_id,
                        {"event_id": f.event_id, "motivo": f.motivo},
                    )
    except Exception as exc:
        # El borrado ya está confirmado: se informa que la etapa de calendario no terminó, sin lanzar.
        _log.error("calendar_scrub_etapa_fallida", extra={"error": type(exc).__name__})
        return {"estado": "error", "eventos": None, "no_limpiados": None}
    return {"estado": "ok", "eventos": len(filas), "no_limpiados": len(fallidos)}


def crear_router_clientes(
    deps: DependenciasDashboard, obtener_pool: Callable[[], Any], fabrica_calendario: FabricaCalendario
) -> APIRouter:
    router = APIRouter(prefix="/clientes", tags=["clientes"])
    ver = Depends(deps.tx_con_permiso(PERMISO_VER))
    gestionar = Depends(deps.tx_con_permiso(PERMISO_GESTIONAR))

    @router.get("")
    async def listar(
        tx: Annotated[Transaccion, ver],
        busqueda: Annotated[str | None, Query(max_length=100)] = None,
        limite: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0, le=2_147_483_647)] = 0,
    ) -> dict[str, Any]:
        filas = await listar_clientes(
            tx.conn, tenant_id=tx.sesion.tenant_id, busqueda=busqueda or None, limite=limite, offset=offset
        )
        return {"clientes": [c.presentar(f) for f in filas], "limite": limite, "offset": offset}

    @router.get("/{cliente_id}")
    async def detalle(cliente_id: uuid.UUID, tx: Annotated[Transaccion, ver]) -> dict[str, Any]:
        fila = await tx.conn.fetchrow(c.SQL_DETALLE, tx.sesion.tenant_id, cliente_id)
        if fila is None:
            raise _error(404, "cliente_no_encontrado", "Cliente no encontrado")
        return c.presentar(fila)

    @router.post("", status_code=201)
    async def alta(datos: AltaCliente, tx: Annotated[Transaccion, gestionar]) -> dict[str, Any]:
        tenant_id = tx.sesion.tenant_id
        telefono = _telefono(datos.phone)
        nombre = _nombre(datos.name)
        extra = await _extra_data(tx.conn, tenant_id, datos.extra_data)
        await _sin_duplicado(tx.conn, tenant_id, telefono, None)
        fila = await tx.conn.fetchrow(c.SQL_INSERTAR, tenant_id, telefono, nombre, json.dumps(extra))
        # Metadata solo con los nombres de los campos: nunca nombre, teléfono ni extra_data.
        await registrar(
            tx.conn,
            "customer_created",
            "end_customers",
            fila["id"],
            {"campos": sorted(datos.model_fields_set & {"phone", "name", "extra_data"})},
        )
        return c.presentar(fila)

    @router.patch("/{cliente_id}")
    async def editar(
        cliente_id: uuid.UUID, datos: EdicionCliente, tx: Annotated[Transaccion, gestionar]
    ) -> dict[str, Any]:
        tenant_id = tx.sesion.tenant_id
        enviados = datos.model_fields_set
        if ("name" in enviados and datos.name is None) or ("phone" in enviados and datos.phone is None):
            raise _error(422, "campo_nulo", "phone y name no pueden ser nulos")
        telefono = _telefono(datos.phone) if datos.phone is not None else None
        nombre = _nombre(datos.name) if datos.name is not None else None
        extra = await _extra_data(tx.conn, tenant_id, datos.extra_data) if datos.extra_data is not None else None
        actual = await tx.conn.fetchrow(c.SQL_DETALLE, tenant_id, cliente_id)
        if actual is None:
            raise _error(404, "cliente_no_encontrado", "Cliente no encontrado")
        if actual["deleted_at"] is not None:
            raise _error(409, "cliente_anonimizado", "El cliente fue anonimizado y no se puede editar")
        if telefono is not None:
            await _sin_duplicado(tx.conn, tenant_id, telefono, cliente_id)
        fila = await tx.conn.fetchrow(
            c.SQL_ACTUALIZAR,
            tenant_id,
            cliente_id,
            telefono is not None,
            telefono,
            nombre is not None,
            nombre,
            extra is not None,
            None if extra is None else json.dumps(extra),
        )
        cambiados = [
            nombre_campo
            for nombre_campo, valor in (("extra_data", extra), ("name", nombre), ("phone", telefono))
            if valor is not None
        ]
        await registrar(tx.conn, "customer_updated", "end_customers", cliente_id, {"campos": cambiados})
        return c.presentar(fila)

    @router.post("/{cliente_id}/anonimizar")
    async def anonimizar(
        cliente_id: uuid.UUID, sesion: Annotated[SesionDashboard, Depends(deps.sesion)]
    ) -> dict[str, Any]:
        pool = obtener_pool()
        # Transacción 1: permiso + anonimización. Salir del `async with` sin error = COMMIT hecho.
        async with tenant_tx(pool, sesion.tenant_id, sesion.user_id) as conn:
            try:
                await verificar_permiso(conn, sesion, PERMISO_ANONIMIZAR)
            except ErrorAuth as e:
                raise http_desde_error_auth(e) from None
            # Inexistente y de otro tenant son indistinguibles (404): el filtro por tenant es explícito.
            if await conn.fetchval(c.SQL_EXISTE, sesion.tenant_id, cliente_id) is None:
                raise _error(404, "cliente_no_encontrado", "Cliente no encontrado")
            try:
                await conn.execute(c.SQL_ANONIMIZAR, cliente_id)
            except Exception as e:
                if getattr(e, "sqlstate", None) == SQLSTATE_PRIVILEGIO_INSUFICIENTE:
                    raise _error(404, "cliente_no_encontrado", "Cliente no encontrado") from None
                raise
        # Transacción 2, ya con el borrado confirmado: calendario (nunca lo deshace).
        calendario = await _limpiar_calendario(pool, sesion, cliente_id, fabrica_calendario)
        # `tenant_id` y `actor_type` ya viajan en el contexto de logs de la sesión; acá solo ids opacos y conteos.
        log_evento_obligatorio(
            "anonymize_end_customer",
            end_customer_id=str(cliente_id),
            calendar_events=calendario["eventos"],
            calendar_not_scrubbed=calendario["no_limpiados"],
        )
        return {"id": str(cliente_id), "sin_cliente_identificable": True, "calendario": calendario}

    return router
