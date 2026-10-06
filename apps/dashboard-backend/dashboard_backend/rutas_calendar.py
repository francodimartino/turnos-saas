"""Conexión del Google Calendar de cada profesional (ARCHITECTURE.md §7.1, §2.11.5, §3.2).

Cada profesional conecta su propio calendario por OAuth2 y el sync es unidireccional (lo hace `platform_calendar`).
Esta conexión la hace el negocio desde el dashboard, no Franco.

  POST   /profesionales/{id}/calendar/conexion   devuelve `{url}` de Google para que el navegador la abra
  GET    /calendar/callback?code&state           vuelta de Google: valida, guarda y redirige al dashboard
  POST   /calendar/callback  `{code, state}`     lo mismo, pero devuelve JSON (para una página del dashboard)
  GET    /profesionales/{id}/calendar            `{conectado, conectado_desde, google_configurado}`
  DELETE /profesionales/{id}/calendar            desconecta y borra el secret de Vault (idempotente)

Permiso (el mismo en las cinco rutas): el profesional vinculado (`professionals.tenant_user_id` = el usuario de la
sesión) conecta, ve y desconecta SU calendario sin permisos extra; para el de cualquier otro profesional del tenant
hace falta `manage_staff` (el owner vivo siempre lo tiene). Un profesional de otro tenant o inexistente es 404.

Seguridad del flujo:
  - El tenant de la sesión (JWT verificado + estado vivo) tiene que ser el del `state` firmado (HMAC, 10 minutos); un
    `state` de otro tenant, vencido o alterado se rechaza como `state_invalido` sin llamar a Google. Además el
    profesional del `state` tiene que ser activo y estar autorizado para esa sesión (se revisa antes de canjear el
    código, así un `state` robado no sirve a quien no podría iniciar la conexión).
  - Se guarda solo el `refresh_token`, por la función SECURITY DEFINER `store_professional_integration` (sin
    escritura directa a `tenant_integrations`). Esa función y `delete_professional_integration` ya dejan la
    auditoría (`connect_google_calendar`/`disconnect_google_calendar`): acá no se registra de nuevo.
  - Ni el `code`, ni el `state`, ni los tokens van a logs, redirecciones o respuestas.
  - Si faltan las variables de Google, iniciar y el callback responden 503 `calendar_no_configurado`; el estado
    informa `google_configurado: false` y desconectar sigue funcionando (solo toca la base).

El callback GET depende de la cookie de sesión en una navegación que viene de google.com: con `SameSite=Strict` el
navegador no la manda (401); para usarlo hay que correr con `COOKIE_SAMESITE=lax`. Con `Strict`, la
`GOOGLE_OAUTH_REDIRECT_URI` apunta a una página del dashboard que hace el `POST /calendar/callback`.
"""

import logging
import uuid
from collections.abc import Callable
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from platform_auth import ErrorAuth, verificar_permiso
from platform_calendar import (
    ErrorApiCalendar,
    ErrorConfiguracionCalendar,
    ErrorEstadoOAuth,
    ErrorOAuth,
    ErrorRedCalendar,
)
from platform_secrets import AccesoSecretoDenegado, crear_almacen
from pydantic import BaseModel, ConfigDict, Field

from dashboard_backend.calendario import FabricaCalendario
from dashboard_backend.dependencias import DependenciasDashboard, Transaccion, http_desde_error_auth

_log = logging.getLogger("dashboard_backend.calendar")

RUTA_DASHBOARD = "/profesionales"
PERMISO_GESTION = "manage_staff"

# Una sola consulta: ¿existe en este tenant?, ¿está activo?, ¿es el profesional vinculado a la sesión?
SQL_PROFESIONAL = """
select p.id, p.is_active, coalesce(u.auth_user_id = $3, false) as es_el_propio
from public.professionals p
left join public.tenant_users u on u.id = p.tenant_user_id and u.tenant_id = p.tenant_id
where p.tenant_id = $1 and p.id = $2
"""
# `dashboard_service` lee de `tenant_integrations` solo columnas sin secretos.
SQL_INTEGRACION = """
select created_at from public.tenant_integrations
where tenant_id = $1 and integration_type = 'google_calendar' and professional_id = $2
"""

# Resultado del callback -> estado HTTP cuando se pide por POST.
_HTTP_POR_RESULTADO = {
    "state_invalido": 400,
    "cancelado": 400,
    "sin_permiso": 403,
    "profesional_invalido": 409,
    "google_rechazo": 502,
    "google_no_disponible": 503,
}
_MENSAJES = {
    "state_invalido": "El enlace de conexión no es válido o venció. Volvé a iniciar la conexión.",
    "cancelado": "No se concedió el acceso a Google Calendar.",
    "sin_permiso": "No tenés permiso para conectar el calendario de ese profesional.",
    "profesional_invalido": "Ese profesional no existe o está inactivo.",
    "google_rechazo": "Google no aceptó la conexión. Probá de nuevo.",
    "google_no_disponible": "No pudimos comunicarnos con Google. Probá de nuevo en un rato.",
}


class CuerpoCallback(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: str = Field(default="", max_length=2048)
    state: str = Field(default="", max_length=2048)
    error: str = Field(default="", max_length=256)


def _error(estado: int, codigo: str, mensaje: str) -> HTTPException:
    return HTTPException(status_code=estado, detail={"codigo": codigo, "mensaje": mensaje})


def _no_configurado() -> HTTPException:
    _log.error("calendar_sin_configurar")
    return _error(
        503,
        "calendar_no_configurado",
        "La conexión con Google Calendar todavía no está disponible. Avisá a soporte.",
    )


async def _autorizar(tx: Transaccion, professional_id: uuid.UUID) -> Any | None:
    """La fila del profesional si existe en el tenant y la sesión puede gestionarlo; `None` si no existe.

    Levanta `ErrorAuth` (403) si existe pero no es el vinculado ni tiene `manage_staff`.
    """
    fila = await tx.conn.fetchrow(SQL_PROFESIONAL, tx.sesion.tenant_id, professional_id, tx.sesion.user_id)
    if fila is None:
        return None
    if fila["es_el_propio"] is not True:
        await verificar_permiso(tx.conn, tx.sesion, PERMISO_GESTION)
    return fila


async def _autorizar_o_http(tx: Transaccion, professional_id: uuid.UUID) -> Any:
    try:
        fila = await _autorizar(tx, professional_id)
    except ErrorAuth as e:
        raise http_desde_error_auth(e) from None
    if fila is None:
        raise _error(404, "no_encontrado", "El profesional no existe.")
    return fila


def crear_router_calendar(
    deps: DependenciasDashboard, fabrica: Callable[..., Any] | FabricaCalendario, dashboard_origin: str
) -> APIRouter:
    router = APIRouter(tags=["calendar"])
    Tx = Annotated[Transaccion, Depends(deps.tx)]

    @router.post("/profesionales/{professional_id}/calendar/conexion")
    async def iniciar(professional_id: uuid.UUID, tx: Tx):
        fila = await _autorizar_o_http(tx, professional_id)
        if fila["is_active"] is not True:
            raise _error(
                409, "profesional_inactivo", "Ese profesional está inactivo: no se puede conectar su calendario."
            )
        try:
            async with fabrica(tx.conn) as servicio:
                url = servicio.url_autorizacion(tx.sesion.tenant_id, professional_id)
        except ErrorConfiguracionCalendar:
            raise _no_configurado() from None
        return {"url": url}

    async def _completar(tx: Transaccion, code: str, state: str, error: str) -> tuple[str, uuid.UUID | None]:
        """Resultado del callback y el profesional del `state` (si el `state` era válido)."""
        try:
            async with fabrica(tx.conn) as servicio:
                try:
                    estado = servicio.verificar_state(state)
                except ErrorEstadoOAuth:
                    return "state_invalido", None
                if estado.tenant_id != tx.sesion.tenant_id:
                    return "state_invalido", None
                profesional = estado.professional_id
                if error or not code:
                    return "cancelado", profesional
                try:
                    fila = await _autorizar(tx, profesional)
                except ErrorAuth:
                    return "sin_permiso", profesional
                if fila is None or fila["is_active"] is not True:
                    return "profesional_invalido", profesional
                try:
                    await servicio.completar_autorizacion(code, state, tx.sesion.tenant_id)
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
        except ErrorConfiguracionCalendar:
            raise _no_configurado() from None

    async def _resolver(tx: Transaccion, code: str, state: str, error: str) -> tuple[str, uuid.UUID | None]:
        resultado, profesional = await _completar(tx, code, state, error)
        _log.info(
            "calendar_callback",
            extra={"resultado": resultado, "profesional_id": None if profesional is None else str(profesional)},
        )
        return resultado, profesional

    @router.get("/calendar/callback")
    async def callback(
        tx: Tx,
        code: Annotated[str, Query(max_length=2048)] = "",
        state: Annotated[str, Query(max_length=2048)] = "",
        error: Annotated[str, Query(max_length=256)] = "",
    ):
        resultado, profesional = await _resolver(tx, code, state, error)
        consulta = {"google_calendar": resultado}
        if profesional is not None:
            consulta["profesional"] = str(profesional)
        return RedirectResponse(f"{dashboard_origin}{RUTA_DASHBOARD}?{urlencode(consulta)}", status_code=303)

    @router.post("/calendar/callback")
    async def callback_json(datos: CuerpoCallback, tx: Tx):
        resultado, profesional = await _resolver(tx, datos.code, datos.state, datos.error)
        if resultado != "ok":
            raise _error(_HTTP_POR_RESULTADO[resultado], resultado, _MENSAJES[resultado])
        return {"resultado": resultado, "profesional_id": str(profesional)}

    @router.get("/profesionales/{professional_id}/calendar")
    async def estado(professional_id: uuid.UUID, tx: Tx):
        await _autorizar_o_http(tx, professional_id)
        integracion = await tx.conn.fetchrow(SQL_INTEGRACION, tx.sesion.tenant_id, professional_id)
        try:
            async with fabrica(tx.conn):
                configurado = True
        except ErrorConfiguracionCalendar:
            configurado = False
        return {
            "profesional_id": str(professional_id),
            "conectado": integracion is not None,
            "conectado_desde": None if integracion is None else integracion["created_at"].isoformat(),
            "google_configurado": configurado,
        }

    @router.delete("/profesionales/{professional_id}/calendar")
    async def desconectar(professional_id: uuid.UUID, tx: Tx):
        await _autorizar_o_http(tx, professional_id)
        habia = await crear_almacen(tx.conn).eliminar_profesional(professional_id)
        return {"profesional_id": str(professional_id), "conectado": False, "habia_conexion": habia}

    return router
