"""Dominio de la conexión del Google Calendar de cada profesional (ARCHITECTURE.md §7.1, §2.11.5, §3.2): sin FastAPI.

Cada profesional conecta su propio calendario por OAuth2 y el sync es unidireccional (lo hace `platform_calendar`).

Permiso: el profesional vinculado (`professionals.tenant_user_id` = el usuario de la sesión) conecta, ve y desconecta SU
calendario sin permisos extra; para el de cualquier otro profesional del tenant hace falta `manage_staff` (el owner
vivo siempre lo tiene). `autorizar` devuelve `None` si el profesional no existe en el tenant y deja subir `ErrorAuth`
si existe pero la sesión no puede gestionarlo.

Flujo del callback, sin conexión del pool tomada durante la espera a Google:
  1. transacción corta de validación (`validar`): sesión viva, `state` firmado, permisos y nonce de un solo uso;
  2. canje del `code` (red);
  3. guardado del refresh token en otra transacción corta (lo hace `completar_autorizacion` a través de la conexión
     corta que entrega `abrir_conexion`).

El `state` va atado al tenant, al profesional y al usuario de la sesión que inició la conexión: se rechaza como
`state_invalido`, sin llamar a Google, si no coinciden, si venció o si fue alterado. El nonce se consume en la base
(`consume_oauth_state_nonce`) después de revisar permisos y antes de canjear el código: un replay devuelve
`state_invalido` y nunca gasta un `code`. Ni el `code`, ni el `state`, ni los tokens van a logs.
"""

import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timezone  # noqa: UP017 (el entorno local puede ser 3.10)
from typing import Any, cast

from platform_auth import ErrorAuth, IdentidadJWT, verificar_permiso
from platform_calendar import (
    ErrorApiCalendar,
    ErrorConfiguracionCalendar,
    ErrorEstadoOAuth,
    ErrorOAuth,
    ErrorRedCalendar,
)
from platform_secrets import AccesoSecretoDenegado, crear_almacen

_log = logging.getLogger("dashboard_backend.calendar")

PERMISO_GESTION = "manage_staff"

# Una sola consulta: ¿existe en este tenant?, ¿está activo?, ¿es el profesional vinculado a la sesión?
SQL_PROFESIONAL = """
select p.id, p.is_active, coalesce(u.auth_user_id = $3, false) as es_el_propio
from public.professionals p
left join public.tenant_users u on u.id = p.tenant_user_id and u.tenant_id = p.tenant_id
where p.tenant_id = $1 and p.id = $2
"""
# `dashboard_service` lee de `tenant_integrations` solo columnas sin secretos.
TIPO_INTEGRACION = "google_calendar"

SQL_INTEGRACION = """
select created_at from public.tenant_integrations
where tenant_id = $1 and integration_type = 'google_calendar' and professional_id = $2
"""

# Nonce de un solo uso del `state`: true la primera vez; false si ya se consumió o venció (ver la migración).
SQL_CONSUMIR_NONCE = "select public.consume_oauth_state_nonce($1, $2)"


async def autorizar(conn: Any, sesion: Any, professional_id: uuid.UUID) -> Any | None:
    """La fila del profesional si existe en el tenant y la sesión puede gestionarlo; `None` si no existe.

    Levanta `ErrorAuth` (403) si existe pero no es el vinculado ni tiene `manage_staff`.
    """
    fila = await conn.fetchrow(SQL_PROFESIONAL, sesion.tenant_id, professional_id, sesion.user_id)
    if fila is None:
        return None
    if fila["es_el_propio"] is not True:
        await verificar_permiso(conn, sesion, PERMISO_GESTION)
    return fila


async def leer_integracion(conn: Any, sesion: Any, professional_id: uuid.UUID) -> Any:
    """La fila (sin secretos) de la integración del profesional, o `None` si no está conectado."""
    return await conn.fetchrow(SQL_INTEGRACION, sesion.tenant_id, professional_id)


async def validar(
    abrir_sesion: Callable[[IdentidadJWT], Any],
    identidad: IdentidadJWT,
    servicio: Any,
    code: str,
    state: str,
    error: str,
) -> tuple[str | None, uuid.UUID | None]:
    """Transacción corta previa al canje: sesión viva, `state`, permisos y nonce de un solo uso.

    Devuelve `(resultado, profesional)`: `resultado` es `None` si se puede canjear el `code`. El nonce se consume
    acá, atómico con los permisos y confirmado antes de llamar a Google.
    """
    async with abrir_sesion(identidad) as tx:
        try:
            estado = servicio.verificar_state(state)
        except ErrorEstadoOAuth:
            return "state_invalido", None
        if estado.tenant_id != tx.sesion.tenant_id or estado.user_id != tx.sesion.user_id:
            return "state_invalido", None
        profesional = estado.professional_id
        if error or not code:
            return "cancelado", profesional
        try:
            fila = await autorizar(tx.conn, tx.sesion, profesional)
        except ErrorAuth:
            return "sin_permiso", profesional
        if fila is None or fila["is_active"] is not True:
            return "profesional_invalido", profesional
        vigente = await tx.conn.fetchval(
            SQL_CONSUMIR_NONCE,
            estado.nonce,
            datetime.fromtimestamp(estado.expira, tz=timezone.utc),  # noqa: UP017
        )
        if vigente is not True:
            return "state_invalido", None  # ya usado (replay) o vencido
        return None, profesional


async def completar(
    abrir_sesion: Callable[[IdentidadJWT], Any],
    abrir_conexion: Callable[[], Any],
    fabrica: Callable[..., Any],
    identidad: IdentidadJWT,
    code: str,
    state: str,
    error: str,
) -> tuple[str, uuid.UUID | None]:
    """Resultado del callback y el profesional del `state` (si el `state` era válido).

    `ErrorConfiguracionCalendar` (Calendar sin configurar) sube al router. `abrir_conexion` entrega una conexión que
    toma el pool solo durante cada consulta.
    """
    async with fabrica(abrir_conexion()) as servicio:
        resultado, profesional = await validar(abrir_sesion, identidad, servicio, code, state, error)
        if resultado is not None:
            return resultado, profesional
        try:
            # La dependencia de identidad ya rechazó las identidades sin tenant_id (no son sesiones de dashboard).
            tenant_id = cast(uuid.UUID, identidad.tenant_id)
            await servicio.completar_autorizacion(code, state, tenant_id, identidad.user_id)
        except ErrorEstadoOAuth:
            return "state_invalido", None
        except ErrorOAuth:
            return "google_rechazo", profesional
        except (ErrorRedCalendar, ErrorApiCalendar):
            return "google_no_disponible", profesional
        except AccesoSecretoDenegado:
            # La base rechazó el guardado (p. ej. el profesional se desactivó justo ahora).
            return "profesional_invalido", profesional
        return "ok", profesional


async def resolver(
    abrir_sesion: Callable[[IdentidadJWT], Any],
    abrir_conexion: Callable[[], Any],
    fabrica: Callable[..., Any],
    identidad: IdentidadJWT,
    code: str,
    state: str,
    error: str,
) -> tuple[str, uuid.UUID | None]:
    """`completar` más el log del resultado (solo el resultado y el id del profesional)."""
    resultado, profesional = await completar(abrir_sesion, abrir_conexion, fabrica, identidad, code, state, error)
    _log.info(
        "calendar_callback",
        extra={"resultado": resultado, "profesional_id": None if profesional is None else str(profesional)},
    )
    return resultado, profesional


async def configurado(fabrica: Callable[..., Any], abrir_conexion: Callable[[], Any]) -> bool:
    """¿Están las variables de Google? Abre y cierra el servicio sin tocar la base ni la red."""
    try:
        async with fabrica(abrir_conexion()):
            return True
    except ErrorConfiguracionCalendar:
        return False


async def desconectar(conn: Any, fabrica: Callable[..., Any], professional_id: uuid.UUID) -> bool:
    """Borra la integración del profesional y descarta su token cacheado; devuelve si había conexión.

    Idempotente. No llama a Google: la fábrica recibe la conexión de la transacción solo para armar el servicio.
    """
    almacen = crear_almacen(conn)
    integracion = await almacen.buscar(TIPO_INTEGRACION, professional_id)
    habia = await almacen.eliminar_profesional(professional_id)
    if integracion is not None:
        # El token de acceso cacheado sigue vigente en memoria hasta una hora: se descarta para que ninguna
        # operación posterior lo use con una conexión que ya no existe.
        try:
            async with fabrica(conn) as servicio:
                servicio.descartar_token(integracion)
        except ErrorConfiguracionCalendar:
            pass  # Calendar no configurado: no hay caché de tokens que limpiar
    return habia
