"""Endpoints `/turnos` y `/packs`: gestión de la agenda desde el dashboard (ARCHITECTURE.md §7.1, §2.6).

Permisos (§2.4), chequeados en vivo dentro de la misma transacción del endpoint (el owner vivo los tiene todos):

  GET  /turnos, GET /turnos/{id}, GET /packs      `view_bookings`
  POST /turnos                                    `approve_bookings`  (alta del negocio: nace `confirmed`)
  POST /turnos/{id}/aprobar | /rechazar           `approve_bookings`, o el profesional vinculado al turno
                                                  (`professionals.tenant_user_id`); solo sobre turnos `pending`
  POST /turnos/{id}/cancelar | /mover | /completar `approve_bookings`  (el negocio es el único que cancela y mueve
                                                  en el piloto; mover y completar solo sobre `confirmed`)
  POST /packs                                     `approve_bookings`  (siempre `origin='manual_grant'`)

No hay DELETE: los turnos se cancelan. Un turno o cliente de otro tenant es indistinguible de uno inexistente (404).

Cada cambio corre en dos etapas, como el borrado de clientes: primero una transacción con el bloqueo del turno, el
chequeo de estado, el cambio y el `audit_log`, que hace COMMIT; recién después, ya con el cambio confirmado, se
sincroniza Google Calendar sin ninguna conexión tomada mientras se espera a Google (el id del evento se guarda en otra
transacción corta; evento solo en `confirmed`: crear al confirmar, actualizar al mover, borrar al cancelar)
y se avisa al cliente por el bot. El aviso se encola en el outbox (`notification_outbox`) dentro de la MISMA
transacción del cambio (si hace rollback no queda aviso); tras el COMMIT se envía en segundo plano con reintentos y
registro en `notifications_log`, y la fila del outbox pasa a `sent`/`failed`. Si el proceso muere antes de enviar, la
fila queda `pending` y el reescaneo del job de vencimiento la envía.
Un fallo de Calendar o del aviso nunca revierte ni oculta un cambio ya confirmado: queda en `audit_log`
(`calendar_sync_failed`) y en los logs, y la respuesta dice qué pasó (`calendario`, `aviso`).
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from platform_audit import registrar
from platform_auth import ErrorAuth, PermisoDenegado, SesionDashboard, verificar_permiso
from platform_notifications import (
    BACKOFF_POR_DEFECTO,
    BotAdapter,
    CompletadorOutboxFuncion,
    EnvioPedido,
    ErrorCredencial,
    RegistradorFuncion,
    adapter_telegram_del_tenant,
    encolar_aviso,
    programar_envio,
)
from platform_notifications.outbox import completar_sin_lanzar
from platform_secrets import crear_almacen
from platform_tenancy import tenant_tx
from pydantic import BaseModel, ConfigDict, Field, field_validator
from turnos_dominio import (
    AvisoLiberado,
    PackNoDisponible,
    crear_turno,
    encolar_avisos_liberados,
    liberar_vencidos_solapados,
    mover_turno,
    pedido_de_envio,
)
from turnos_queries import detalle_booking, listar_bookings, listar_packs
from turnos_queries.bloqueo import bloquear_profesional

from dashboard_backend import turnos as t
from dashboard_backend.calendario import ConexionBajoDemanda, FabricaCalendario
from dashboard_backend.dependencias import DependenciasDashboard, Transaccion, http_desde_error_auth
from dashboard_backend.errores_db import horario_ocupado, reintentar_transaccion, traducir_errores_db

PERMISO_VER = "view_bookings"
PERMISO_GESTIONAR = "approve_bookings"

MAX_SESIONES_PACK = 1000

_log = logging.getLogger("dashboard_backend.turnos")

FabricaAdaptador = Callable[[Any, "httpx.AsyncClient | None"], Awaitable[BotAdapter]]


async def _adaptador_telegram(conn: Any, cliente: httpx.AsyncClient | None) -> BotAdapter:
    """Bot del tenant del contexto de la transacción (`find_integration` + secret de la integración)."""
    return await adapter_telegram_del_tenant(crear_almacen(conn), cliente)


@dataclass
class OpcionesAvisos:
    """Cómo se avisa al cliente. Los valores por defecto son los de producción; los tests inyectan los suyos."""

    cliente_http: httpx.AsyncClient | None = None
    fabrica_adaptador: FabricaAdaptador = field(default=_adaptador_telegram)
    backoff: Sequence[float] = BACKOFF_POR_DEFECTO
    esperar: Callable[[float], Awaitable[None]] = asyncio.sleep


def _aware(valor: datetime) -> datetime:
    if valor.tzinfo is None or valor.tzinfo.utcoffset(valor) is None:
        raise ValueError("la fecha debe incluir zona horaria (por ejemplo 2030-03-04T10:00:00-03:00)")
    return valor


class AltaTurno(BaseModel):
    # `extra="ignore"`: un `tenant_id` o un `status` en el body se descartan; el tenant sale de la sesión.
    model_config = ConfigDict(extra="ignore")

    end_customer_id: uuid.UUID
    service_id: uuid.UUID
    professional_id: uuid.UUID
    start_at: datetime
    booking_pack_id: uuid.UUID | None = None

    @field_validator("start_at")
    @classmethod
    def _inicio_con_zona(cls, valor: datetime) -> datetime:
        return _aware(valor)


class MoverTurno(BaseModel):
    model_config = ConfigDict(extra="ignore")

    start_at: datetime

    @field_validator("start_at")
    @classmethod
    def _inicio_con_zona(cls, valor: datetime) -> datetime:
        return _aware(valor)


class AltaPack(BaseModel):
    # `origin` y `payment_status` no se aceptan del cliente: acá siempre es `manual_grant`.
    model_config = ConfigDict(extra="ignore")

    end_customer_id: uuid.UUID
    service_id: uuid.UUID
    total_sessions: int = Field(ge=1, le=MAX_SESIONES_PACK)
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def _vencimiento_con_zona(cls, valor: datetime | None) -> datetime | None:
        return None if valor is None else _aware(valor)


def _error(estado: int, codigo: str, mensaje: str) -> HTTPException:
    return HTTPException(status_code=estado, detail={"codigo": codigo, "mensaje": mensaje})


def _no_encontrado() -> HTTPException:
    return _error(404, "turno_no_encontrado", "Turno no encontrado")


def _tx(pool: Any, sesion: SesionDashboard):
    return tenant_tx(pool, sesion.tenant_id, sesion.user_id)


async def _cargar(conn: Any, tenant_id: uuid.UUID, turno_id: uuid.UUID) -> Any:
    fila = await conn.fetchrow(t.SQL_TURNO, tenant_id, turno_id)
    if fila is None:
        raise _no_encontrado()
    return fila


async def _autorizar_y_cargar(
    conn: Any, sesion: SesionDashboard, turno_id: uuid.UUID, *, profesional_vinculado: bool
) -> Any:
    """Exige `approve_bookings` (o, si se permite, ser el profesional vinculado al turno) y bloquea el turno.

    Quien no tiene permiso recibe el mismo 403 si el turno no existe, es de otro tenant o es de otro profesional.
    """
    try:
        await verificar_permiso(conn, sesion, PERMISO_GESTIONAR)
    except PermisoDenegado as e:
        if profesional_vinculado:
            fila = await conn.fetchrow(t.SQL_TURNO, sesion.tenant_id, turno_id)
            if fila is not None and fila["professional_auth_user_id"] == sesion.user_id:
                return fila
        raise http_desde_error_auth(e) from None
    except ErrorAuth as e:
        raise http_desde_error_auth(e) from None
    return await _cargar(conn, sesion.tenant_id, turno_id)


def _exigir_estado(fila: Any, validos: tuple[str, ...], accion: str) -> None:
    if fila["status"] not in validos:
        raise _error(409, "estado_invalido", f"No se puede {accion} un turno en estado '{fila['status']}'.")


async def _auditar_aparte(pool: Any, sesion: SesionDashboard, accion: str, turno_id: uuid.UUID, meta: dict) -> None:
    """Registra en `audit_log` en una transacción propia. Nunca lanza."""
    try:
        async with _tx(pool, sesion) as conn:
            await registrar(conn, accion, "bookings", turno_id, meta)
    except Exception as exc:  # noqa: BLE001 - el cambio ya está confirmado; solo se deja rastro en logs
        _log.error("auditoria_fallida", extra={"accion": accion, "error": type(exc).__name__})


async def _sincronizar_calendario(
    pool: Any,
    sesion: SesionDashboard,
    fabrica: FabricaCalendario,
    fila: Any,
    estado: str,
    inicio: datetime,
    fin: datetime,
) -> dict[str, Any]:
    """Aplica el estado del turno a Calendar y persiste el id del evento. Nunca lanza: un fallo se registra."""
    resultado = None
    motivo = None
    # Ninguna conexión queda tomada mientras se espera a Google: el servicio lee la base con transacciones cortas
    # propias (`ConexionBajoDemanda`) y el id del evento se guarda en otra transacción corta, después.
    try:
        async with fabrica(ConexionBajoDemanda(lambda: _tx(pool, sesion))) as servicio:
            resultado = await servicio.sincronizar_booking(t.booking_para_calendario(fila, estado, inicio, fin))
    except Exception as exc:  # noqa: BLE001 - Calendar no configurado, token vencido, Google caído...
        motivo = type(exc).__name__
    if motivo is None and resultado is not None and resultado.event_id != fila["google_calendar_event_id"]:
        try:
            async with _tx(pool, sesion) as conn:
                await conn.execute(t.SQL_GUARDAR_EVENTO, sesion.tenant_id, fila["id"], resultado.event_id)
        except Exception as exc:  # noqa: BLE001
            motivo = type(exc).__name__
    if motivo is not None:
        _log.error("calendar_sync_fallo", extra={"motivo": motivo})
        await _auditar_aparte(pool, sesion, "calendar_sync_failed", fila["id"], {"estado": estado, "motivo": motivo})
        return {"estado": "error", "evento": fila["google_calendar_event_id"]}
    assert resultado is not None  # noqa: S101 - estrechamiento de tipo para mypy: sin motivo de error hay resultado
    return {"estado": resultado.accion.value, "evento": resultado.event_id}


@dataclass(frozen=True)
class AvisoEncolado:
    """Resultado de encolar el aviso dentro de la transacción del cambio: `estado` es lo que ve el llamador."""

    estado: str
    outbox_id: uuid.UUID | None = None


SIN_AVISO = AvisoEncolado("no_aplica")


async def _encolar_aviso(conn: Any, fila: Any, plantilla: str | None, inicio: datetime) -> AvisoEncolado:
    """Encola el aviso en la transacción de `conn`. Sin plantilla, cliente anonimizado o canal sin soporte, no encola.

    `params` lleva solo identificadores y el inicio del turno: el texto y el destinatario se resuelven al enviar.
    """
    if plantilla is None:
        return SIN_AVISO
    if fila["customer_deleted_at"] is not None:
        return AvisoEncolado("cliente_anonimizado")
    if not fila["customer_channel"] or not fila["customer_channel_user_id"]:
        return AvisoEncolado("sin_canal")
    if fila["customer_channel"] != "telegram":
        return AvisoEncolado("canal_no_soportado")
    outbox_id = await encolar_aviso(
        conn,
        end_customer_id=fila["end_customer_id"],
        channel=fila["customer_channel"],
        template=plantilla,
        params={"booking_id": str(fila["id"]), "inicio": inicio.isoformat()},
    )
    if outbox_id is None:  # anonimizado entre la lectura y el encolado
        return AvisoEncolado("cliente_anonimizado")
    return AvisoEncolado("encolado", outbox_id)


async def _avisar(
    pool: Any,
    sesion: SesionDashboard,
    tareas: BackgroundTasks,
    opciones: OpcionesAvisos,
    fila: Any,
    plantilla: str,
    inicio: datetime,
    aviso: AvisoEncolado,
) -> str:
    """Programa el envío del aviso ya encolado (post-COMMIT). Devuelve qué pasó; nunca lanza."""
    if aviso.outbox_id is None:
        return aviso.estado
    completador = CompletadorOutboxFuncion(lambda: _tx(pool, sesion))
    try:
        async with _tx(pool, sesion) as conn:
            adapter = await opciones.fabrica_adaptador(conn, opciones.cliente_http)
    except ErrorCredencial:
        await completar_sin_lanzar(completador, aviso.outbox_id, "failed", "sin_integracion")
        return "sin_integracion"
    except Exception as exc:  # noqa: BLE001 - la fila sigue pending: el reescaneo la reintenta
        _log.error("aviso_sin_adapter", extra={"error": type(exc).__name__})
        return "error"
    pedido = EnvioPedido(
        end_customer_id=fila["end_customer_id"],
        destinatario=fila["customer_channel_user_id"],
        texto=t.texto_aviso(plantilla, fila, inicio),
        template=plantilla,
        outbox_id=aviso.outbox_id,
    )
    registrador = RegistradorFuncion(lambda: _tx(pool, sesion))
    programar_envio(
        tareas, adapter, registrador, pedido, backoff=opciones.backoff, esperar=opciones.esperar, outbox=completador
    )
    return "programado"


async def _avisar_liberados(
    pool: Any,
    sesion: SesionDashboard,
    tareas: BackgroundTasks,
    opciones: OpcionesAvisos,
    avisos: Sequence[AvisoLiberado],
) -> None:
    """Programa el envío (post-COMMIT) del aviso de vencimiento a cada cliente liberado. Nunca lanza.

    Los avisos ya están encolados en el outbox, en la transacción del alta o del movimiento. Sin integración la
    fila queda `failed`; ante cualquier otro error queda `pending` y el reescaneo del job de vencimiento la envía.
    """
    if not avisos:
        return
    completador = CompletadorOutboxFuncion(lambda: _tx(pool, sesion))
    try:
        async with _tx(pool, sesion) as conn:
            adapter = await opciones.fabrica_adaptador(conn, opciones.cliente_http)
    except ErrorCredencial:
        for aviso in avisos:
            await completar_sin_lanzar(completador, aviso.outbox_id, "failed", "sin_integracion")
        return
    except Exception as exc:  # noqa: BLE001 - las filas siguen pending: el reescaneo las reintenta
        _log.error("aviso_liberados_sin_adapter", extra={"error": type(exc).__name__})
        return
    registrador = RegistradorFuncion(lambda: _tx(pool, sesion))
    for aviso in avisos:
        programar_envio(
            tareas,
            adapter,
            registrador,
            pedido_de_envio(aviso),
            backoff=opciones.backoff,
            esperar=opciones.esperar,
            outbox=completador,
        )


def crear_router_turnos(
    deps: DependenciasDashboard,
    obtener_pool: Callable[[], Any],
    fabrica_calendario: FabricaCalendario,
    opciones_avisos: OpcionesAvisos | None = None,
) -> APIRouter:
    router = APIRouter(tags=["turnos"])
    ver = Depends(deps.tx_con_permiso(PERMISO_VER))
    sesion_dep = Depends(deps.sesion)
    opciones = opciones_avisos or OpcionesAvisos()

    async def _cerrar(
        pool: Any,
        sesion: SesionDashboard,
        tareas: BackgroundTasks,
        fila: Any,
        *,
        estado: str,
        inicio: datetime,
        fin: datetime,
        sincronizar: bool,
        plantilla: str | None,
        aviso_encolado: AvisoEncolado = SIN_AVISO,
        liberados: Sequence[AvisoLiberado] = (),
    ) -> dict[str, Any]:
        """Etapa posterior al COMMIT: Calendar, envío de los avisos ya encolados y respuesta."""
        calendario: dict[str, Any] = {"estado": "no_aplica"}
        evento = fila["google_calendar_event_id"]
        if sincronizar and (estado == "confirmed" or evento):
            sync = await _sincronizar_calendario(pool, sesion, fabrica_calendario, fila, estado, inicio, fin)
            evento = sync.pop("evento")
            calendario = sync
        aviso = "no_aplica"
        if plantilla is not None:
            aviso = await _avisar(pool, sesion, tareas, opciones, fila, plantilla, inicio, aviso_encolado)
        await _avisar_liberados(pool, sesion, tareas, opciones, liberados)
        actual = {**dict(fila), "status": estado, "start_at": inicio, "end_at": fin, "google_calendar_event_id": evento}
        if estado != "pending":
            actual["pending_expires_at"] = None
        respuesta = t.presentar_turno(actual, fila["customer_deleted_at"] is not None)
        respuesta["calendario"] = calendario
        respuesta["aviso"] = aviso
        return respuesta

    @router.get("/turnos")
    async def listar(
        tx: Annotated[Transaccion, ver],
        desde: Annotated[datetime, Query()],
        hasta: Annotated[datetime, Query()],
        estado: Annotated[list[str] | None, Query()] = None,
        professional_id: uuid.UUID | None = None,
        limite: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0, le=2_147_483_647)] = 0,
    ) -> dict[str, Any]:
        for fecha in (desde, hasta):
            if fecha.tzinfo is None:
                raise _error(422, "fecha_sin_zona", "Las fechas deben incluir zona horaria.")
        if hasta <= desde:
            raise _error(422, "rango_invalido", "'hasta' debe ser posterior a 'desde'.")
        if estado and any(e not in t.ESTADOS for e in estado):
            raise _error(422, "estado_desconocido", f"Estados válidos: {', '.join(t.ESTADOS)}.")
        tenant_id = tx.sesion.tenant_id
        filas = await listar_bookings(
            tx.conn,
            tenant_id=tenant_id,
            desde=desde,
            hasta=hasta,
            estados=estado or None,
            professional_id=professional_id,
            limite=limite,
            offset=offset,
        )
        anonimos = await _anonimizados(tx.conn, tenant_id, filas)
        return {
            "turnos": [t.presentar_turno(f, f["end_customer_id"] in anonimos) for f in filas],
            "limite": limite,
            "offset": offset,
        }

    async def _anonimizados(conn: Any, tenant_id: uuid.UUID, filas: list[dict[str, Any]]) -> set[uuid.UUID]:
        ids = sorted({f["end_customer_id"] for f in filas}, key=str)
        if not ids:
            return set()
        return {r["id"] for r in await conn.fetch(t.SQL_CLIENTES_ELIMINADOS, tenant_id, ids)}

    @router.get("/turnos/{turno_id}")
    async def detalle(turno_id: uuid.UUID, tx: Annotated[Transaccion, ver]) -> dict[str, Any]:
        tenant_id = tx.sesion.tenant_id
        fila = await detalle_booking(tx.conn, tenant_id=tenant_id, booking_id=turno_id)
        if fila is None:
            raise _no_encontrado()
        anonimos = await _anonimizados(tx.conn, tenant_id, [fila])
        return t.presentar_turno(fila, fila["end_customer_id"] in anonimos)

    @router.post("/turnos", status_code=201)
    async def crear(datos: AltaTurno, sesion: Annotated[SesionDashboard, sesion_dep], tareas: BackgroundTasks):
        """Alta del negocio, a través de `turnos_dominio.crear_turno`.

        Nace `confirmed`, sin vencimiento (no pasa por aprobación), sin los límites de reserva del bot y sin exigir
        disponibilidad: el negocio puede forzar un horario fuera de atención. Como el bot, libera los pendientes
        vencidos que ocupan el horario (los cancela y avisa a sus clientes por el outbox); solo lo frena un choque
        con otro turno del profesional, incluido un pendiente todavía vigente (409). Con `booking_pack_id` consume
        una sesión del pack en la misma operación.
        """
        pool = obtener_pool()
        tenant_id = sesion.tenant_id

        async def intento() -> tuple[Any, tuple[AvisoLiberado, ...]]:
            async with _tx(pool, sesion) as conn:
                try:
                    await verificar_permiso(conn, sesion, PERMISO_GESTIONAR)
                except ErrorAuth as e:
                    raise http_desde_error_auth(e) from None
                alta = await conn.fetchrow(
                    t.SQL_DATOS_ALTA, tenant_id, datos.service_id, datos.professional_id, datos.end_customer_id
                )
                if alta is None:
                    raise _error(
                        422, "referencia_invalida", "El cliente, servicio o profesional no existe en este local."
                    )
                if alta["customer_deleted_at"] is not None:
                    raise _error(
                        409, "cliente_anonimizado", "El cliente fue anonimizado: no se le pueden agendar turnos."
                    )
                if not alta["service_active"]:
                    raise _error(422, "servicio_inactivo", "El servicio está desactivado.")
                if not alta["professional_active"]:
                    raise _error(422, "profesional_inactivo", "El profesional está desactivado.")
                if not alta["ofrece"]:
                    raise _error(422, "profesional_no_ofrece_servicio", "Ese profesional no hace ese servicio.")
                fin = datos.start_at + timedelta(minutes=alta["duration_minutes"])
                with traducir_errores_db():
                    try:
                        # Alta del negocio: `confirmed`, sin vencimiento, sin límites del bot y sin exigir
                        # disponibilidad; libera los pendientes vencidos. Un deadlock se propaga: reintenta.
                        creado = await crear_turno(
                            conn,
                            tenant_id=tenant_id,
                            end_customer_id=datos.end_customer_id,
                            servicio_id=datos.service_id,
                            profesional_id=datos.professional_id,
                            inicio=datos.start_at,
                            fin=fin,
                            estado="confirmed",
                            booking_pack_id=datos.booking_pack_id,
                            liberar_vencidos=True,
                            concurrencia_como_choque=False,
                        )
                    except PackNoDisponible:
                        raise _error(
                            409,
                            "pack_no_disponible",
                            "El pack no existe, no es de ese cliente o servicio, venció o no tiene sesiones.",
                        ) from None
                    if creado is None:
                        raise horario_ocupado()
                    turno_id = creado.id
                    await registrar(conn, "booking_created", "bookings", turno_id, {"a": "confirmed"})
                    # Los avisos a los liberados se encolan en esta misma transacción (rollback: no quedan).
                    avisos = await encolar_avisos_liberados(conn, creado.liberados)
                return await _cargar(conn, tenant_id, turno_id), avisos

        fila, liberados = await reintentar_transaccion(intento)
        return await _cerrar(
            pool, sesion, tareas, fila, estado="confirmed", inicio=fila["start_at"], fin=fila["end_at"],
            sincronizar=True, plantilla=None, liberados=liberados,
        )  # fmt: skip

    async def _cambiar_estado(
        turno_id: uuid.UUID,
        sesion: SesionDashboard,
        tareas: BackgroundTasks,
        *,
        validos: tuple[str, ...],
        verbo: str,
        nuevo: str,
        accion_audit: str,
        plantilla: str | None,
        profesional_vinculado: bool = False,
    ) -> dict[str, Any]:
        pool = obtener_pool()
        async with _tx(pool, sesion) as conn:
            fila = await _autorizar_y_cargar(conn, sesion, turno_id, profesional_vinculado=profesional_vinculado)
            _exigir_estado(fila, validos, verbo)
            if nuevo == "confirmed" and fila["vencido"]:
                raise _error(409, "turno_vencido", "La solicitud venció: el turno ya no se puede aprobar.")
            with traducir_errores_db():
                await conn.execute(t.SQL_CAMBIAR_ESTADO, sesion.tenant_id, turno_id, nuevo)
                await registrar(conn, accion_audit, "bookings", turno_id, {"de": fila["status"], "a": nuevo})
            aviso = await _encolar_aviso(conn, fila, plantilla, fila["start_at"])  # misma transacción del cambio
        return await _cerrar(
            pool, sesion, tareas, fila, estado=nuevo, inicio=fila["start_at"], fin=fila["end_at"],
            sincronizar=nuevo != "completed", plantilla=plantilla, aviso_encolado=aviso,
        )  # fmt: skip

    @router.post("/turnos/{turno_id}/aprobar")
    async def aprobar(turno_id: uuid.UUID, sesion: Annotated[SesionDashboard, sesion_dep], tareas: BackgroundTasks):
        return await _cambiar_estado(
            turno_id, sesion, tareas, validos=("pending",), verbo="aprobar", nuevo="confirmed",
            accion_audit="booking_approved", plantilla="turno_confirmado", profesional_vinculado=True,
        )  # fmt: skip

    @router.post("/turnos/{turno_id}/rechazar")
    async def rechazar(turno_id: uuid.UUID, sesion: Annotated[SesionDashboard, sesion_dep], tareas: BackgroundTasks):
        return await _cambiar_estado(
            turno_id, sesion, tareas, validos=("pending",), verbo="rechazar", nuevo="cancelled",
            accion_audit="booking_rejected", plantilla="turno_rechazado", profesional_vinculado=True,
        )  # fmt: skip

    @router.post("/turnos/{turno_id}/cancelar")
    async def cancelar(turno_id: uuid.UUID, sesion: Annotated[SesionDashboard, sesion_dep], tareas: BackgroundTasks):
        # La sesión del pack la devuelve el trigger de la base al pasar a 'cancelled': acá no se toca `used_sessions`.
        return await _cambiar_estado(
            turno_id, sesion, tareas, validos=("pending", "confirmed"), verbo="cancelar", nuevo="cancelled",
            accion_audit="booking_cancelled", plantilla="turno_cancelado",
        )  # fmt: skip

    @router.post("/turnos/{turno_id}/completar")
    async def completar(turno_id: uuid.UUID, sesion: Annotated[SesionDashboard, sesion_dep], tareas: BackgroundTasks):
        return await _cambiar_estado(
            turno_id, sesion, tareas, validos=("confirmed",), verbo="completar", nuevo="completed",
            accion_audit="booking_completed", plantilla=None,
        )  # fmt: skip

    @router.post("/turnos/{turno_id}/mover")
    async def mover(
        turno_id: uuid.UUID, datos: MoverTurno, sesion: Annotated[SesionDashboard, sesion_dep], tareas: BackgroundTasks
    ):
        pool = obtener_pool()

        async def intento() -> tuple[Any, datetime, datetime, AvisoEncolado, tuple[AvisoLiberado, ...]]:
            async with _tx(pool, sesion) as conn:
                fila = await _autorizar_y_cargar(conn, sesion, turno_id, profesional_vinculado=False)
                _exigir_estado(fila, ("confirmed",), "mover")
                inicio = datos.start_at
                fin = inicio + (fila["end_at"] - fila["start_at"])  # misma duración que el turno original
                with traducir_errores_db():
                    # Como el alta: libera los pendientes vencidos que ocupan el horario nuevo, bajo el lock del
                    # profesional (el mismo que toma `mover_turno`; es reentrante dentro de la transacción).
                    await bloquear_profesional(conn, tenant_id=sesion.tenant_id, profesional_id=fila["professional_id"])
                    liberados = await liberar_vencidos_solapados(
                        conn, profesional_id=fila["professional_id"], inicio=inicio, fin=fin
                    )
                    resultado = await mover_turno(
                        conn,
                        tenant_id=sesion.tenant_id,
                        booking_id=turno_id,
                        profesional_id=fila["professional_id"],
                        inicio=inicio,
                        fin=fin,
                        concurrencia_como_choque=False,
                    )
                    if resultado == "choque":
                        raise horario_ocupado()
                    if resultado == "no_existe":
                        raise _no_encontrado()
                    await registrar(
                        conn,
                        "booking_moved",
                        "bookings",
                        turno_id,
                        {"start_at_anterior": fila["start_at"].isoformat(), "start_at_nuevo": inicio.isoformat()},
                    )
                aviso = await _encolar_aviso(conn, fila, "turno_movido", inicio)  # misma transacción del cambio
                avisos = await encolar_avisos_liberados(conn, liberados)
                return fila, inicio, fin, aviso, avisos

        fila, inicio, fin, aviso, liberados = await reintentar_transaccion(intento)
        return await _cerrar(
            pool, sesion, tareas, fila, estado="confirmed", inicio=inicio, fin=fin,
            sincronizar=True, plantilla="turno_movido", aviso_encolado=aviso, liberados=liberados,
        )  # fmt: skip

    @router.get("/packs")
    async def listar_los_packs(
        tx: Annotated[Transaccion, ver],
        end_customer_id: uuid.UUID | None = None,
        limite: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0, le=2_147_483_647)] = 0,
    ) -> dict[str, Any]:
        filas = await listar_packs(
            tx.conn, tenant_id=tx.sesion.tenant_id, end_customer_id=end_customer_id, limite=limite, offset=offset
        )
        return {"packs": [t.presentar_pack(f) for f in filas], "limite": limite, "offset": offset}

    @router.post("/packs", status_code=201)
    async def asignar_pack(datos: AltaPack, sesion: Annotated[SesionDashboard, sesion_dep]) -> dict[str, Any]:
        pool = obtener_pool()
        async with _tx(pool, sesion) as conn:
            try:
                await verificar_permiso(conn, sesion, PERMISO_GESTIONAR)
            except ErrorAuth as e:
                raise http_desde_error_auth(e) from None
            cliente = await conn.fetchrow(t.SQL_CLIENTE_PACK, sesion.tenant_id, datos.end_customer_id)
            if cliente is None:
                raise _error(404, "cliente_no_encontrado", "Cliente no encontrado")
            if cliente["deleted_at"] is not None:
                raise _error(409, "cliente_anonimizado", "El cliente fue anonimizado: no se le pueden asignar packs.")
            with traducir_errores_db():
                fila = await conn.fetchrow(
                    t.SQL_INSERTAR_PACK,
                    sesion.tenant_id,
                    datos.end_customer_id,
                    datos.service_id,
                    datos.total_sessions,
                    datos.expires_at,
                )
                await registrar(
                    conn,
                    "booking_pack_granted",
                    "booking_packs",
                    fila["id"],
                    {"total_sessions": datos.total_sessions},
                )
        return t.presentar_pack(fila)

    return router
