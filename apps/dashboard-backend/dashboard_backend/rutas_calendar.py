"""Conexión del Google Calendar de cada profesional (ARCHITECTURE.md §7.1, §2.11.5, §3.2).

Cada profesional conecta su propio calendario por OAuth2 y el sync es unidireccional (lo hace `platform_calendar`).
Esta conexión la hace el negocio desde el dashboard, no Franco.

  POST   /profesionales/{id}/calendar/conexion   devuelve `{url}` de Google para que el navegador la abra
  GET    /calendar/callback                      sin efecto: redirige al dashboard (no escribe, no llama a Google)
  POST   /calendar/callback  `{code, state}`     vuelta de Google: valida, guarda y devuelve JSON
  GET    /profesionales/{id}/calendar            `{conectado, conectado_desde, google_configurado}`
  DELETE /profesionales/{id}/calendar            desconecta y borra el secret de Vault (idempotente)

Este módulo solo traduce HTTP: la autorización, el flujo del callback y el SQL están en `conexion_calendar`, que no
conoce FastAPI. La fábrica de Calendar y el origen del dashboard salen de `app.state` con `Depends`. Las rutas que
hablan con Google (iniciar y el callback) usan transacciones cortas y una conexión bajo demanda, así que ninguna
retiene una conexión del pool mientras espera a Google; `desconectar` usa una sola transacción porque no hace red.

Permiso (el mismo en las rutas de conexión): el profesional vinculado (`professionals.tenant_user_id` = el usuario de la
sesión) conecta, ve y desconecta SU calendario sin permisos extra; para el de cualquier otro profesional del tenant
hace falta `manage_staff` (el owner vivo siempre lo tiene). Un profesional de otro tenant o inexistente es 404.

Seguridad del flujo:
  - La conexión se completa SOLO por `POST /calendar/callback`, que pasa por el chequeo de `Origin` del dashboard
    (un sitio ajeno no puede dispararlo con la cookie de la víctima). El `GET /calendar/callback` no lee `code` ni
    `state`, no toca la base ni llama a Google: solo redirige al dashboard. Por eso la cookie de sesión puede quedar
    en `SameSite=Strict`: `GOOGLE_OAUTH_REDIRECT_URI` apunta a la página del dashboard (`/calendar/callback` del
    frontend) que lee `code` y `state` de la URL y hace el POST.
  - El `state` firmado (HMAC, 10 minutos) lleva el tenant, el profesional, el usuario de la sesión que inició la
    conexión y un nonce. Se rechaza como `state_invalido`, sin llamar a Google, si el tenant o el usuario de la sesión
    que vuelve no son los del `state` (un `state` robado no sirve a otra persona, ni del mismo tenant), si venció o
    si fue alterado.
  - El nonce es de un solo uso: se consume en la base (`consume_oauth_state_nonce`) antes de canjear el código, así
    reusar el mismo `state` (replay) devuelve `state_invalido`. Se consume después de revisar permisos para que quien
    no puede conectar a ese profesional no gaste el nonce.
  - El profesional del `state` tiene que ser activo y estar autorizado para esa sesión (se revisa antes de canjear el
    código).
  - Se guarda solo el `refresh_token`, por la función SECURITY DEFINER `store_professional_integration` (sin
    escritura directa a `tenant_integrations`). Esa función y `delete_professional_integration` ya dejan la
    auditoría (`connect_google_calendar`/`disconnect_google_calendar`, con el usuario que actuó y solo el id del
    profesional): acá no se registra de nuevo.
  - Ni el `code`, ni el `state`, ni los tokens van a logs, redirecciones o respuestas.
  - Si faltan las variables de Google, iniciar y el callback responden 503 `calendar_no_configurado`; el estado
    informa `google_configurado: false` y desconectar sigue funcionando (solo toca la base).
"""

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from platform_auth import ErrorAuth, IdentidadJWT
from platform_calendar import ErrorConfiguracionCalendar
from platform_tenancy import tenant_tx
from pydantic import BaseModel, ConfigDict, Field

from dashboard_backend import conexion_calendar as calendar
from dashboard_backend.calendario import ConexionBajoDemanda
from dashboard_backend.dependencias import (
    Transaccion,
    dependencias_de,
    http_desde_error_auth,
    identidad_actual,
    transaccion,
)

_log = logging.getLogger("dashboard_backend.calendar")

router = APIRouter(tags=["calendar"])

RUTA_DASHBOARD = "/profesionales"

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


def fabrica_calendario(request: Request) -> Any:
    """Fábrica de servicios de Calendar de la app (`app.state.calendario`)."""
    return request.app.state.calendario


def origen_dashboard(request: Request) -> str:
    """Origen del dashboard (`app.state.config.dashboard_origin`): a dónde vuelve el `GET /calendar/callback`."""
    return request.app.state.config.dashboard_origin


Identidad = Annotated[IdentidadJWT, Depends(identidad_actual)]
Fabrica = Annotated[Any, Depends(fabrica_calendario)]
Origen = Annotated[str, Depends(origen_dashboard)]
Tx = Annotated[Transaccion, Depends(transaccion)]


def _conexion_corta(request: Request, identidad: IdentidadJWT) -> Any:
    """Función que entrega conexiones que toman el pool solo durante cada consulta."""
    pool = dependencias_de(request).obtener_pool
    return lambda: ConexionBajoDemanda(lambda: tenant_tx(pool(), identidad.tenant_id, identidad.user_id))


async def _autorizar_o_http(tx: Transaccion, professional_id: uuid.UUID) -> Any:
    try:
        fila = await calendar.autorizar(tx.conn, tx.sesion, professional_id)
    except ErrorAuth as e:
        raise http_desde_error_auth(e) from None
    if fila is None:
        raise _error(404, "no_encontrado", "El profesional no existe.")
    return fila


@router.post("/profesionales/{professional_id}/calendar/conexion")
async def iniciar(professional_id: uuid.UUID, request: Request, identidad: Identidad, fabrica: Fabrica):
    # Transacción corta solo para autorizar; la URL se arma (sin red ni base) con la transacción ya cerrada.
    async with dependencias_de(request).abrir_sesion(identidad) as tx:
        fila = await _autorizar_o_http(tx, professional_id)
        sesion = tx.sesion
    if fila["is_active"] is not True:
        raise _error(409, "profesional_inactivo", "Ese profesional está inactivo: no se puede conectar su calendario.")
    try:
        async with fabrica(_conexion_corta(request, identidad)()) as servicio:
            url = servicio.url_autorizacion(sesion.tenant_id, professional_id, sesion.user_id)
    except ErrorConfiguracionCalendar:
        raise _no_configurado() from None
    return {"url": url}


@router.get("/calendar/callback")
async def callback(origen: Origen):
    # Sin efecto: no lee `code`/`state`, no toca la base ni Google, no exige sesión. La conexión se completa
    # solo por el POST (con chequeo de Origin); si Google redirigiera acá, el negocio vuelve al dashboard.
    return RedirectResponse(f"{origen}{RUTA_DASHBOARD}", status_code=303)


@router.post("/calendar/callback")
async def callback_json(datos: CuerpoCallback, request: Request, identidad: Identidad, fabrica: Fabrica):
    try:
        resultado, profesional = await calendar.resolver(
            dependencias_de(request).abrir_sesion,
            _conexion_corta(request, identidad),
            fabrica,
            identidad,
            datos.code,
            datos.state,
            datos.error,
        )
    except ErrorConfiguracionCalendar:
        raise _no_configurado() from None
    if resultado != "ok":
        raise _error(_HTTP_POR_RESULTADO[resultado], resultado, _MENSAJES[resultado])
    return {"resultado": resultado, "profesional_id": str(profesional)}


@router.get("/profesionales/{professional_id}/calendar")
async def estado(professional_id: uuid.UUID, request: Request, identidad: Identidad, fabrica: Fabrica):
    # Transacción corta solo para autorizar y leer; la configuración se consulta con la transacción ya cerrada.
    async with dependencias_de(request).abrir_sesion(identidad) as tx:
        await _autorizar_o_http(tx, professional_id)
        integracion = await calendar.leer_integracion(tx.conn, tx.sesion, professional_id)
    configurado = await calendar.configurado(fabrica, _conexion_corta(request, identidad))
    return {
        "profesional_id": str(professional_id),
        "conectado": integracion is not None,
        "conectado_desde": None if integracion is None else integracion["created_at"].isoformat(),
        "google_configurado": configurado,
    }


@router.delete("/profesionales/{professional_id}/calendar")
async def desconectar(professional_id: uuid.UUID, tx: Tx, fabrica: Fabrica):
    await _autorizar_o_http(tx, professional_id)
    habia = await calendar.desconectar(tx.conn, fabrica, professional_id)
    return {"profesional_id": str(professional_id), "conectado": False, "habia_conexion": habia}
