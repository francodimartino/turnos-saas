"""Reglas de la gestión de `end_customers` y del derecho al olvido (ARCHITECTURE.md §2.3, §2.8, §3.5): sin FastAPI.

Cada función recibe la conexión de la transacción que abrió el endpoint (con tenant y actor ya fijados, y el permiso
ya verificado) y devuelve datos o lanza un error de dominio que el router traduce a HTTP. El SQL está en `clientes`.
La auditoría se escribe en la misma transacción que el cambio: si falla, el cambio se revierte.

Normalización: el teléfono se guarda en E.164 (`normalizar_e164`, solo dígitos ASCII) y el email en minúsculas y
sin espacios (`normalizar_email`); `TelefonoInvalido`, `ErrorEmailInvalido` y `ErrorExtraData` suben tal cual.

Unir dos fichas (`unir`): la principal conserva su email, teléfono y nombre y completa los vacíos con los de la
absorbida; los datos de la absorbida que chocan con los de la principal no se conservan. Se rechaza el mismo tipo de
canal con identificadores distintos (`ErrorCanalEnConflicto`).

Precedencia de los choques: primero el email, después el teléfono. Si existe una ficha con ese email se responde
`ErrorEmailExistente` con su id, aunque el teléfono sea de otra ficha; recién sin choque de email se mira el teléfono.
El nombre se recorta y no puede quedar vacío.

La anonimización se hace en dos etapas a propósito. `anonimizar` corre dentro de la transacción del endpoint (que
también cubre la sesión viva y el permiso); recién con el COMMIT hecho, `limpiar_calendario` abre transacciones cortas
propias: lee los eventos a limpiar, llama a Google sin conexión tomada (red externa) y registra `calendar_scrub_failed`
por cada evento que no se pudo limpiar. Una falla del calendario nunca deshace ni oculta un borrado ya confirmado.
"""

import json
import logging
import uuid
from typing import Any

from platform_audit import registrar
from platform_auth import SesionDashboard
from platform_calendar import ErrorConfiguracionCalendar, EventoNoLimpiado
from platform_tenancy import ConexionBajoDemanda, tenant_tx
from turnos_negocio.calendario import TEXTO_EVENTO_ANONIMIZADO
from turnos_queries import listar_clientes
from turnos_telefonos import normalizar_e164

from dashboard_backend import clientes as c
from dashboard_backend.calendario import FabricaCalendario

MOTIVO_SIN_INTEGRACION = "sin_integracion"
MOTIVO_CONFIGURACION = "configuracion_calendar"
MOTIVO_INESPERADO = "error_inesperado"

SQLSTATE_PRIVILEGIO_INSUFICIENTE = "42501"
SQLSTATE_UNICIDAD = "23505"

_log = logging.getLogger("dashboard_backend.clientes")


class ErrorCliente(Exception):
    """Base de los errores de la gestión de clientes."""


class ErrorNombreInvalido(ErrorCliente):
    """El nombre quedó vacío tras recortarlo."""


class ErrorCampoNulo(ErrorCliente):
    """`phone` o `name` vinieron explícitamente en `null` en una edición."""


class ErrorClienteInexistente(ErrorCliente):
    """No existe en este tenant (otro tenant e inexistente son indistinguibles)."""


class ErrorClienteAnonimizado(ErrorCliente):
    """El cliente ya fue anonimizado y no admite ediciones."""


class ErrorUnionClienteAnonimizado(ErrorClienteAnonimizado):
    """Una de las dos fichas a unir ya fue anonimizada."""


class ErrorUnionMismaFicha(ErrorCliente):
    """Se pidió unir una ficha consigo misma."""


class ErrorCanalEnConflicto(ErrorCliente):
    """Las dos fichas tienen el mismo tipo de canal con identificadores distintos: no se decide solo cuál queda."""


class ErrorClienteDuplicado(ErrorCliente):
    """Ya hay un cliente no anonimizado con ese teléfono en el tenant."""

    def __init__(self, cliente_id: uuid.UUID):
        super().__init__(str(cliente_id))
        self.cliente_id = cliente_id


class ErrorEmailExistente(ErrorCliente):
    """Ya hay una ficha del tenant con ese email."""

    def __init__(self, cliente_id: uuid.UUID):
        super().__init__(str(cliente_id))
        self.cliente_id = cliente_id


def _nombre(valor: str) -> str:
    nombre = valor.strip()
    if not nombre:
        raise ErrorNombreInvalido
    return nombre


async def _extra_data(conn: Any, tenant_id: uuid.UUID, extra: dict[str, Any]) -> dict[str, Any]:
    if not extra:
        return {}
    settings = c.a_json(await conn.fetchval(c.SQL_SETTINGS, tenant_id)) or {}
    campos = settings.get(c.CAMPOS_EXTRA_SETTING) if isinstance(settings, dict) else None
    return c.validar_extra_data(extra, campos)


async def _sin_duplicado(conn: Any, tenant_id: uuid.UUID, telefono: str, excluir: uuid.UUID | None) -> None:
    await conn.fetchval(c.SQL_BLOQUEO_ALTA, tenant_id, telefono)
    existente = await conn.fetchval(c.SQL_DUPLICADO, tenant_id, telefono, excluir)
    if existente is not None:
        raise ErrorClienteDuplicado(existente)


async def _sin_email_repetido(conn: Any, tenant_id: uuid.UUID, email: str, excluir: uuid.UUID | None) -> None:
    # Serializa por email dentro del tenant; la unicidad real la garantiza además el índice de la base.
    await conn.fetchval(c.SQL_BLOQUEO_ALTA, tenant_id, "email:" + email)
    existente = await conn.fetchval(c.SQL_EMAIL_DUPLICADO, tenant_id, email, excluir)
    if existente is not None:
        raise ErrorEmailExistente(existente)


async def _canales_por_ficha(conn: Any, tenant_id: uuid.UUID, ids: list[uuid.UUID]) -> dict[str, list[dict[str, Any]]]:
    """Canales (tipo y verificado, nunca el identificador) agrupados por ficha."""
    agrupados: dict[str, list[dict[str, Any]]] = {}
    for f in await conn.fetch(c.SQL_CANALES, tenant_id, ids):
        agrupados.setdefault(str(f["end_customer_id"]), []).append(
            {"canal": f["canal"], "verificado": bool(f["verificado"])}
        )
    return agrupados


async def _presentar(conn: Any, tenant_id: uuid.UUID, fila: Any) -> dict[str, Any]:
    canales = await _canales_por_ficha(conn, tenant_id, [fila["id"]])
    return c.presentar(fila, canales.get(str(fila["id"])))


async def listar(conn: Any, tenant_id: uuid.UUID, busqueda: str | None, limite: int, offset: int) -> dict[str, Any]:
    filas = await listar_clientes(conn, tenant_id=tenant_id, busqueda=busqueda or None, limite=limite, offset=offset)
    ids = [f["id"] for f in filas]
    emails: dict[str, Any] = {}
    canales: dict[str, list[dict[str, Any]]] = {}
    if ids:
        emails = {str(f["id"]): f["email"] for f in await conn.fetch(c.SQL_EMAILS, tenant_id, ids)}
        canales = await _canales_por_ficha(conn, tenant_id, ids)
    return {
        "clientes": [c.presentar({**f, "email": emails.get(str(f["id"]))}, canales.get(str(f["id"]))) for f in filas],
        "limite": limite,
        "offset": offset,
    }


async def detalle(conn: Any, tenant_id: uuid.UUID, cliente_id: uuid.UUID) -> dict[str, Any]:
    fila = await conn.fetchrow(c.SQL_DETALLE, tenant_id, cliente_id)
    if fila is None:
        raise ErrorClienteInexistente
    return await _presentar(conn, tenant_id, fila)


async def _escribir_con_unicidad(
    conn: Any, tenant_id: uuid.UUID, email: str | None, excluir: uuid.UUID | None, sql: str, *args: Any
) -> Any:
    """Escritura que puede chocar con el índice de email: un choque (carrera) sale como email repetido, no como 500."""
    # Savepoint: el choque aborta la transacción y, sin volver a él, no se podría buscar la ficha que ya tiene el email.
    await conn.execute(c.SQL_SAVEPOINT_ESCRITURA)
    try:
        fila = await conn.fetchrow(sql, *args)
    except Exception as e:
        await conn.execute(c.SQL_VOLVER_AL_SAVEPOINT)
        if getattr(e, "sqlstate", None) != SQLSTATE_UNICIDAD or email is None:
            raise
        existente = await conn.fetchval(c.SQL_EMAIL_DUPLICADO, tenant_id, email, excluir)
        if existente is None:
            raise
        raise ErrorEmailExistente(existente) from None
    await conn.execute(c.SQL_LIBERAR_SAVEPOINT)
    return fila


async def crear(
    conn: Any,
    tenant_id: uuid.UUID,
    phone: str,
    name: str,
    extra_data: dict[str, Any],
    enviados: set[str],
    email: str | None = None,
) -> dict[str, Any]:
    telefono = normalizar_e164(phone)
    nombre = _nombre(name)
    correo = c.normalizar_email(email) if email is not None else None
    extra = await _extra_data(conn, tenant_id, extra_data)
    if correo is not None:
        await _sin_email_repetido(conn, tenant_id, correo, None)
    await _sin_duplicado(conn, tenant_id, telefono, None)
    fila = await _escribir_con_unicidad(
        conn, tenant_id, correo, None, c.SQL_INSERTAR, tenant_id, telefono, nombre, json.dumps(extra), correo
    )
    # Metadata solo con los nombres de los campos: nunca nombre, teléfono, email ni extra_data.
    campos = sorted(enviados & {"phone", "name", "extra_data", "email"})
    await registrar(conn, "customer_created", "end_customers", fila["id"], {"campos": campos})
    return c.presentar(fila)


async def editar(
    conn: Any,
    tenant_id: uuid.UUID,
    cliente_id: uuid.UUID,
    phone: str | None,
    name: str | None,
    extra_data: dict[str, Any] | None,
    enviados: set[str],
    email: str | None = None,
) -> dict[str, Any]:
    if ("name" in enviados and name is None) or ("phone" in enviados and phone is None):
        raise ErrorCampoNulo
    telefono = normalizar_e164(phone) if phone is not None else None
    nombre = _nombre(name) if name is not None else None
    # `email` enviado en null borra el email de la ficha; sin enviar, no se toca.
    cambia_email = "email" in enviados
    correo = c.normalizar_email(email) if email is not None else None
    extra = await _extra_data(conn, tenant_id, extra_data) if extra_data is not None else None
    actual = await conn.fetchrow(c.SQL_DETALLE, tenant_id, cliente_id)
    if actual is None:
        raise ErrorClienteInexistente
    if actual["deleted_at"] is not None:
        raise ErrorClienteAnonimizado
    if correo is not None:
        await _sin_email_repetido(conn, tenant_id, correo, cliente_id)
    if telefono is not None:
        await _sin_duplicado(conn, tenant_id, telefono, cliente_id)
    fila = await _escribir_con_unicidad(
        conn,
        tenant_id,
        correo,
        cliente_id,
        c.SQL_ACTUALIZAR,
        tenant_id,
        cliente_id,
        telefono is not None,
        telefono,
        nombre is not None,
        nombre,
        extra is not None,
        None if extra is None else json.dumps(extra),
        cambia_email,
        correo,
    )
    cambiados = [
        nombre_campo
        for nombre_campo, valor in (("extra_data", extra), ("name", nombre), ("phone", telefono))
        if valor is not None
    ]
    if cambia_email:
        cambiados.append("email")
    await registrar(conn, "customer_updated", "end_customers", cliente_id, {"campos": sorted(cambiados)})
    return await _presentar(conn, tenant_id, fila)


async def anonimizar(conn: Any, tenant_id: uuid.UUID, cliente_id: uuid.UUID) -> None:
    """Primera etapa del borrado, dentro de la transacción del endpoint (el permiso ya está verificado)."""
    # Inexistente y de otro tenant son indistinguibles (404): el filtro por tenant es explícito.
    if await conn.fetchval(c.SQL_EXISTE, tenant_id, cliente_id) is None:
        raise ErrorClienteInexistente
    try:
        await conn.execute(c.SQL_ANONIMIZAR, cliente_id)
    except Exception as e:
        if getattr(e, "sqlstate", None) == SQLSTATE_PRIVILEGIO_INSUFICIENTE:
            raise ErrorClienteInexistente from None
        raise


async def unir(conn: Any, tenant_id: uuid.UUID, principal_id: uuid.UUID, absorbida_id: uuid.UUID) -> dict[str, Any]:
    """Une dos fichas de la misma persona dentro de la transacción del endpoint (el permiso ya está verificado).

    La ficha `principal_id` queda; la `absorbida_id` se anonimiza. El trabajo es de la función de la base, que bloquea
    las dos filas en un orden fijo, valida contra el tenant del contexto y audita en la misma transacción; acá solo se
    traducen sus errores. Una ficha de otro tenant, una inexistente y un contexto sin tenant son indistinguibles.
    """
    if principal_id == absorbida_id:
        raise ErrorUnionMismaFicha
    try:
        movido = c.a_json(await conn.fetchval(c.SQL_UNIR, principal_id, absorbida_id))
    except Exception as e:
        sqlstate = getattr(e, "sqlstate", None)
        if sqlstate in (c.UNION_FICHA_INEXISTENTE, SQLSTATE_PRIVILEGIO_INSUFICIENTE):
            raise ErrorClienteInexistente from None
        if sqlstate == c.UNION_FICHA_ANONIMIZADA:
            raise ErrorUnionClienteAnonimizado from None
        if sqlstate == c.UNION_CANAL_EN_CONFLICTO:
            raise ErrorCanalEnConflicto from None
        if sqlstate == c.UNION_MISMA_FICHA:
            raise ErrorUnionMismaFicha from None
        raise
    return {"cliente": await detalle(conn, tenant_id, principal_id), "absorbida_id": str(absorbida_id), **movido}


async def limpiar_calendario(
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
            EventoNoLimpiado(None, f["google_calendar_event_id"], MOTIVO_SIN_INTEGRACION)  # type: ignore[arg-type]  # sin integración: integration_id es None
            for f in filas
            if f["integration_id"] is None
        ]
        if pares:
            try:
                abrir_tx = lambda: tenant_tx(pool, sesion.tenant_id, sesion.user_id)  # noqa: E731
                async with fabrica(ConexionBajoDemanda(abrir_tx)) as servicio:
                    fallidos.extend(await servicio.anonimizar_eventos(pares, TEXTO_EVENTO_ANONIMIZADO))
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
