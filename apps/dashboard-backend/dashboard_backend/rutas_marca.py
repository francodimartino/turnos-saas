"""Marca del local: logo, colores y plazo de cancelación (`tenants.settings`, ARCHITECTURE.md §2.12).

  POST   /local/logo        manage_schedule   cuerpo: los bytes de la imagen (PNG, JPG o WebP, hasta 1 MB)
  DELETE /local/logo        manage_schedule
  PATCH  /local/apariencia  manage_schedule   `color_principal`, `color_secundario` (#RRGGBB), `cancelacion_plazo_horas`

El logo llega como cuerpo binario (no multipart) y su tipo se decide por los bytes, nunca por el `Content-Type` ni el
nombre del archivo. El tope de 1 MB lo cumple el middleware de límite de body (413) y, por defensa, el cliente de
Storage (413). Un tipo inválido es 422 `logo_tipo_invalido`; settings que superarían el tope, 422
`settings_demasiado_grandes`; otra operación del logo que empezó después, 409 `logo_reemplazado`;
Storage caído o sin configurar, 503
`almacenamiento_no_disponible` (nunca 500). Los errores no reflejan lo recibido. Las reglas y la orquestación están en
`marca_local`; este módulo solo traduce HTTP.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from platform_auth import IdentidadJWT, Permiso
from turnos_almacenamiento import (
    AlmacenamientoNoDisponible,
    ArchivoDemasiadoGrande,
    ErrorAlmacenamiento,
    TipoNoPermitido,
)
from turnos_dominio.settings_tenant import MAX_BYTES_SETTINGS
from turnos_http import error_http

from dashboard_backend import marca_local, settings_local
from dashboard_backend.dependencias import dependencias_de, identidad_actual
from dashboard_backend.errores_db import traducir_errores_db
from dashboard_backend.esquemas_catalogo import AparienciaParche
from dashboard_backend.respuestas_settings import SettingsOut

router = APIRouter(tags=["local"])

Identidad = Annotated[IdentidadJWT, Depends(identidad_actual)]

_BINARIO = {"schema": {"type": "string", "format": "binary"}}
_CUERPO_LOGO = {
    "requestBody": {
        "required": True,
        "content": {tipo: _BINARIO for tipo in ("application/octet-stream", "image/png", "image/jpeg", "image/webp")},
    }
}


def _abrir_tx(request: Request, identidad: IdentidadJWT):
    def abrir():
        return dependencias_de(request).abrir_con_permiso(identidad, Permiso.GESTIONAR_AGENDA)

    return abrir


ERRORES_DOMINIO = (
    settings_local.ErrorSettingsDemasiadoGrandes,
    marca_local.ErrorLogoReemplazado,
    settings_local.ErrorLocalInexistente,
)


def _http_desde_dominio(error: Exception) -> HTTPException:
    if isinstance(error, settings_local.ErrorSettingsDemasiadoGrandes):
        return error_http(
            422,
            "settings_demasiado_grandes",
            f"los settings del local superarían el máximo de {MAX_BYTES_SETTINGS // 1000} KB",
        )
    if isinstance(error, marca_local.ErrorLogoReemplazado):
        return error_http(409, "logo_reemplazado", "Otra operación sobre el logo se hizo mientras tanto. Reintentá.")
    return error_http(404, "no_encontrado", "El local no existe.")


def _http_desde_almacenamiento(error: ErrorAlmacenamiento) -> HTTPException:
    if isinstance(error, TipoNoPermitido):
        return error_http(422, "logo_tipo_invalido", "El logo debe ser una imagen PNG, JPG o WebP.")
    if isinstance(error, ArchivoDemasiadoGrande):
        return error_http(413, "logo_demasiado_grande", "El logo supera el máximo de 1 MB.")
    if isinstance(error, (AlmacenamientoNoDisponible, marca_local.ErrorAlmacenamientoNoConfigurado)):
        exc = error_http(
            503, "almacenamiento_no_disponible", "No se pudo guardar el logo ahora. Probá de nuevo en unos minutos."
        )
        exc.headers = {"Retry-After": "5"}
        return exc
    return error_http(422, "logo_invalido", "No se pudo procesar el logo.")


@router.post("/local/logo", response_model=SettingsOut, response_model_exclude_unset=True, openapi_extra=_CUERPO_LOGO)
async def subir_logo(request: Request, identidad: Identidad) -> Any:
    contenido = await request.body()
    try:
        with traducir_errores_db():
            settings = await marca_local.subir_logo(
                _abrir_tx(request, identidad), request.app.state.almacenamiento, contenido
            )
    except ErrorAlmacenamiento as e:
        raise _http_desde_almacenamiento(e) from None
    except ERRORES_DOMINIO as e:
        raise _http_desde_dominio(e) from None
    return {"settings": settings}


@router.delete("/local/logo", response_model=SettingsOut, response_model_exclude_unset=True)
async def borrar_logo(request: Request, identidad: Identidad) -> Any:
    try:
        with traducir_errores_db():
            settings = await marca_local.borrar_logo(_abrir_tx(request, identidad), request.app.state.almacenamiento)
    except ErrorAlmacenamiento as e:
        raise _http_desde_almacenamiento(e) from None
    except ERRORES_DOMINIO as e:
        raise _http_desde_dominio(e) from None
    return {"settings": settings}


@router.patch("/local/apariencia", response_model=SettingsOut, response_model_exclude_unset=True)
async def modificar_apariencia(datos: AparienciaParche, request: Request, identidad: Identidad) -> Any:
    try:
        with traducir_errores_db():
            settings = await marca_local.modificar_apariencia(_abrir_tx(request, identidad), datos.cambios())
    except marca_local.ErrorApariencia as e:
        raise error_http(422, "apariencia_invalida", str(e)) from None
    except ERRORES_DOMINIO as e:
        raise _http_desde_dominio(e) from None
    return {"settings": settings}
