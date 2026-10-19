"""Reglas de la gestión de turnos y packs desde el dashboard (ARCHITECTURE.md §7.1, §2.6): sin FastAPI.

Cada función recibe la conexión de la transacción que abrió el endpoint (con tenant y actor ya fijados, la sesión viva
ya leída y, donde corresponde, el permiso ya verificado) y devuelve datos o lanza un `ErrorTurno` que el router
traduce a HTTP (`estado`, `codigo` y `mensaje` son lo que ve quien llama la API). El SQL está en `turnos`.

Alta, movimiento y cambio de estado devuelven un `CambioConfirmado`: lo que quedó hecho dentro de la transacción
(bloqueo, cambio, `audit_log` y avisos encolados en el outbox, todo junto: un rollback no deja nada) y lo que falta
hacer después del COMMIT, que resuelve `cierre_turnos.cerrar` (Calendar, envío de los avisos).

El alta y el movimiento pasan por `turnos_dominio.crear_turno` y `mover_turno`, los mismos que usa el bot, con el lock
del profesional tomado primero. Liberan los pendientes vencidos que ocupan el horario y encolan sus avisos en la misma
transacción. Un deadlock o un fallo de serialización se propaga tal cual: el router repite la transacción entera
(`reintentar_transaccion`).
"""

import uuid
from datetime import datetime, timedelta
from typing import Any, ClassVar

from platform_audit import registrar
from platform_auth import PermisoDenegado, SesionDashboard, verificar_permiso
from turnos_dominio import (
    PackNoDisponible,
    crear_turno,
    encolar_avisos_liberados,
    liberar_vencidos_solapados,
    mover_turno,
)
from turnos_queries import detalle_booking, listar_bookings, listar_packs
from turnos_queries.bloqueo import bloquear_profesional

from dashboard_backend import turnos as t
from dashboard_backend.avisos_turnos import encolar_aviso_del_turno
from dashboard_backend.cierre_turnos import CambioConfirmado
from dashboard_backend.esquemas_turnos import AltaPack, AltaTurno, MoverTurno

PERMISO_VER = "view_bookings"
PERMISO_GESTIONAR = "approve_bookings"


class ErrorTurno(Exception):
    """Base de los errores de la gestión de turnos: cada subclase trae el estado HTTP, el código y el mensaje."""

    estado: ClassVar[int] = 500
    codigo: ClassVar[str] = "error_interno"
    mensaje: ClassVar[str] = "Error interno"

    def __init__(self, mensaje: str | None = None):
        super().__init__(mensaje or self.mensaje)
        self.texto = mensaje or self.mensaje


class TurnoNoEncontrado(ErrorTurno):
    estado, codigo, mensaje = 404, "turno_no_encontrado", "Turno no encontrado"


class ClienteNoEncontrado(ErrorTurno):
    estado, codigo, mensaje = 404, "cliente_no_encontrado", "Cliente no encontrado"


class HorarioOcupado(ErrorTurno):
    estado, codigo = 409, "horario_ocupado"
    mensaje = "Ese horario se superpone con otro turno del profesional. Elegí otro horario."


class EstadoInvalido(ErrorTurno):
    estado, codigo = 409, "estado_invalido"

    def __init__(self, accion: str, estado_actual: str):
        super().__init__(f"No se puede {accion} un turno en estado '{estado_actual}'.")


class TurnoVencido(ErrorTurno):
    estado, codigo, mensaje = 409, "turno_vencido", "La solicitud venció: el turno ya no se puede aprobar."


class ClienteAnonimizado(ErrorTurno):
    estado, codigo = 409, "cliente_anonimizado"
    mensaje = "El cliente fue anonimizado: no se le pueden agendar turnos."


class ClienteAnonimizadoParaPack(ClienteAnonimizado):
    mensaje = "El cliente fue anonimizado: no se le pueden asignar packs."


class PackNoDisponibleParaTurno(ErrorTurno):
    estado, codigo = 409, "pack_no_disponible"
    mensaje = "El pack no existe, no es de ese cliente o servicio, venció o no tiene sesiones."


class ReferenciaInvalida(ErrorTurno):
    estado, codigo = 422, "referencia_invalida"
    mensaje = "El cliente, servicio o profesional no existe en este local."


class ServicioInactivo(ErrorTurno):
    estado, codigo, mensaje = 422, "servicio_inactivo", "El servicio está desactivado."


class ProfesionalInactivo(ErrorTurno):
    estado, codigo, mensaje = 422, "profesional_inactivo", "El profesional está desactivado."


class ProfesionalNoOfreceServicio(ErrorTurno):
    estado, codigo, mensaje = 422, "profesional_no_ofrece_servicio", "Ese profesional no hace ese servicio."


class FechaSinZona(ErrorTurno):
    estado, codigo, mensaje = 422, "fecha_sin_zona", "Las fechas deben incluir zona horaria."


class RangoInvalido(ErrorTurno):
    estado, codigo, mensaje = 422, "rango_invalido", "'hasta' debe ser posterior a 'desde'."


class EstadoDesconocido(ErrorTurno):
    estado, codigo = 422, "estado_desconocido"

    def __init__(self) -> None:
        super().__init__(f"Estados válidos: {', '.join(t.ESTADOS)}.")


async def _cargar(conn: Any, tenant_id: uuid.UUID, turno_id: uuid.UUID) -> Any:
    fila = await conn.fetchrow(t.SQL_TURNO, tenant_id, turno_id)
    if fila is None:
        raise TurnoNoEncontrado
    return fila


async def autorizar_y_cargar(
    conn: Any, sesion: SesionDashboard, turno_id: uuid.UUID, *, profesional_vinculado: bool
) -> Any:
    """Exige `approve_bookings` (o, si se permite, ser el profesional vinculado al turno) y bloquea el turno.

    Quien no tiene permiso recibe el mismo error si el turno no existe, es de otro tenant o es de otro profesional.
    Un `ErrorAuth` sube tal cual: el router lo traduce.
    """
    try:
        await verificar_permiso(conn, sesion, PERMISO_GESTIONAR)
    except PermisoDenegado:
        if profesional_vinculado:
            fila = await conn.fetchrow(t.SQL_TURNO, sesion.tenant_id, turno_id)
            if fila is not None and fila["professional_auth_user_id"] == sesion.user_id:
                return fila
        raise
    return await _cargar(conn, sesion.tenant_id, turno_id)


def _exigir_estado(fila: Any, validos: tuple[str, ...], accion: str) -> None:
    if fila["status"] not in validos:
        raise EstadoInvalido(accion, fila["status"])


async def _anonimizados(conn: Any, tenant_id: uuid.UUID, filas: list[dict[str, Any]]) -> set[uuid.UUID]:
    ids = sorted({f["end_customer_id"] for f in filas}, key=str)
    if not ids:
        return set()
    return {r["id"] for r in await conn.fetch(t.SQL_CLIENTES_ELIMINADOS, tenant_id, ids)}


async def listar(
    conn: Any,
    tenant_id: uuid.UUID,
    desde: datetime,
    hasta: datetime,
    estados: list[str] | None,
    professional_id: uuid.UUID | None,
    limite: int,
    offset: int,
) -> dict[str, Any]:
    for fecha in (desde, hasta):
        if fecha.tzinfo is None:
            raise FechaSinZona
    if hasta <= desde:
        raise RangoInvalido
    if estados and any(e not in t.ESTADOS for e in estados):
        raise EstadoDesconocido
    filas = await listar_bookings(
        conn,
        tenant_id=tenant_id,
        desde=desde,
        hasta=hasta,
        estados=estados or None,
        professional_id=professional_id,
        limite=limite,
        offset=offset,
    )
    anonimos = await _anonimizados(conn, tenant_id, filas)
    return {
        "turnos": [t.presentar_turno(f, f["end_customer_id"] in anonimos) for f in filas],
        "limite": limite,
        "offset": offset,
    }


async def detalle(conn: Any, tenant_id: uuid.UUID, turno_id: uuid.UUID) -> dict[str, Any]:
    fila = await detalle_booking(conn, tenant_id=tenant_id, booking_id=turno_id)
    if fila is None:
        raise TurnoNoEncontrado
    anonimos = await _anonimizados(conn, tenant_id, [fila])
    return t.presentar_turno(fila, fila["end_customer_id"] in anonimos)


async def crear(conn: Any, tenant_id: uuid.UUID, datos: AltaTurno) -> CambioConfirmado:
    """Alta del negocio, a través de `turnos_dominio.crear_turno` (el permiso ya está verificado).

    Nace `confirmed`, sin vencimiento (no pasa por aprobación), sin los límites de reserva del bot y sin exigir
    disponibilidad: el negocio puede forzar un horario fuera de atención. Como el bot, libera los pendientes
    vencidos que ocupan el horario (los cancela y avisa a sus clientes por el outbox); solo lo frena un choque
    con otro turno del profesional, incluido un pendiente todavía vigente (409). Con `booking_pack_id` consume
    una sesión del pack en la misma operación.
    """
    alta = await conn.fetchrow(
        t.SQL_DATOS_ALTA, tenant_id, datos.service_id, datos.professional_id, datos.end_customer_id
    )
    if alta is None:
        raise ReferenciaInvalida
    if alta["customer_deleted_at"] is not None:
        raise ClienteAnonimizado
    if not alta["service_active"]:
        raise ServicioInactivo
    if not alta["professional_active"]:
        raise ProfesionalInactivo
    if not alta["ofrece"]:
        raise ProfesionalNoOfreceServicio
    fin = datos.start_at + timedelta(minutes=alta["duration_minutes"])
    try:
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
        raise PackNoDisponibleParaTurno from None
    if creado is None:
        raise HorarioOcupado
    await registrar(conn, "booking_created", "bookings", creado.id, {"a": "confirmed"})
    # Los avisos a los liberados se encolan en esta misma transacción (rollback: no quedan).
    avisos = await encolar_avisos_liberados(conn, creado.liberados)
    fila = await _cargar(conn, tenant_id, creado.id)
    return CambioConfirmado(
        fila, "confirmed", fila["start_at"], fila["end_at"], sincronizar=True, plantilla=None, liberados=avisos
    )


async def cambiar_estado(
    conn: Any,
    sesion: SesionDashboard,
    turno_id: uuid.UUID,
    *,
    validos: tuple[str, ...],
    verbo: str,
    nuevo: str,
    accion_audit: str,
    plantilla: str | None,
    profesional_vinculado: bool = False,
) -> CambioConfirmado:
    """Aprobar, rechazar, cancelar o completar: bloquea el turno, valida el estado, cambia, audita y encola el aviso."""
    fila = await autorizar_y_cargar(conn, sesion, turno_id, profesional_vinculado=profesional_vinculado)
    _exigir_estado(fila, validos, verbo)
    if nuevo == "confirmed" and fila["vencido"]:
        raise TurnoVencido
    await conn.execute(t.SQL_CAMBIAR_ESTADO, sesion.tenant_id, turno_id, nuevo)
    await registrar(conn, accion_audit, "bookings", turno_id, {"de": fila["status"], "a": nuevo})
    aviso = await encolar_aviso_del_turno(conn, fila, plantilla, fila["start_at"])  # misma transacción del cambio
    return CambioConfirmado(
        fila,
        nuevo,
        fila["start_at"],
        fila["end_at"],
        sincronizar=nuevo != "completed",
        plantilla=plantilla,
        aviso=aviso,
    )


async def mover(conn: Any, sesion: SesionDashboard, turno_id: uuid.UUID, datos: MoverTurno) -> CambioConfirmado:
    """Mueve un turno `confirmed` conservando su duración, liberando los pendientes vencidos del horario nuevo."""
    fila = await autorizar_y_cargar(conn, sesion, turno_id, profesional_vinculado=False)
    _exigir_estado(fila, ("confirmed",), "mover")
    inicio = datos.start_at
    fin = inicio + (fila["end_at"] - fila["start_at"])  # misma duración que el turno original
    # Como el alta: libera los pendientes vencidos que ocupan el horario nuevo, bajo el lock del profesional
    # (el mismo que toma `mover_turno`; es reentrante dentro de la transacción).
    await bloquear_profesional(conn, tenant_id=sesion.tenant_id, profesional_id=fila["professional_id"])
    liberados = await liberar_vencidos_solapados(conn, profesional_id=fila["professional_id"], inicio=inicio, fin=fin)
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
        raise HorarioOcupado
    if resultado == "no_existe":
        raise TurnoNoEncontrado
    await registrar(
        conn,
        "booking_moved",
        "bookings",
        turno_id,
        {"start_at_anterior": fila["start_at"].isoformat(), "start_at_nuevo": inicio.isoformat()},
    )
    aviso = await encolar_aviso_del_turno(conn, fila, "turno_movido", inicio)  # misma transacción del cambio
    avisos = await encolar_avisos_liberados(conn, liberados)
    return CambioConfirmado(
        fila, "confirmed", inicio, fin, sincronizar=True, plantilla="turno_movido", aviso=aviso, liberados=avisos
    )


async def listar_los_packs(
    conn: Any, tenant_id: uuid.UUID, end_customer_id: uuid.UUID | None, limite: int, offset: int
) -> dict[str, Any]:
    filas = await listar_packs(conn, tenant_id=tenant_id, end_customer_id=end_customer_id, limite=limite, offset=offset)
    return {"packs": [t.presentar_pack(f) for f in filas], "limite": limite, "offset": offset}


async def asignar_pack(conn: Any, tenant_id: uuid.UUID, datos: AltaPack) -> dict[str, Any]:
    """Alta manual de un pack (siempre `origin='manual_grant'`); el permiso ya está verificado."""
    cliente = await conn.fetchrow(t.SQL_CLIENTE_PACK, tenant_id, datos.end_customer_id)
    if cliente is None:
        raise ClienteNoEncontrado
    if cliente["deleted_at"] is not None:
        raise ClienteAnonimizadoParaPack
    fila = await conn.fetchrow(
        t.SQL_INSERTAR_PACK,
        tenant_id,
        datos.end_customer_id,
        datos.service_id,
        datos.total_sessions,
        datos.expires_at,
    )
    await registrar(conn, "booking_pack_granted", "booking_packs", fila["id"], {"total_sessions": datos.total_sessions})
    return t.presentar_pack(fila)
