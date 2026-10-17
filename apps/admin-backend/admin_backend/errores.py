"""Traducción de errores de `platform_auth` y `platform_admin` a respuestas HTTP.

Formato único: `{"detail": {"codigo": ..., "mensaje": ...}}`. Los mensajes de dominio son genéricos y nunca
incluyen emails ni credenciales; lo que no sea de estas jerarquías es un fallo real (500 sin detalle).
"""

from fastapi import HTTPException
from platform_admin import (
    BotYaConectado,
    ErrorDominioAdmin,
    InvitacionNoPendiente,
    PurgaEnCurso,
    ReduccionSinConfirmar,
    SlugEnUso,
    TenantNoEncontrado,
    TransicionInvalida,
)
from platform_auth import ErrorAuth


def http_desde_error_auth(error: ErrorAuth) -> HTTPException:
    headers = {"WWW-Authenticate": "Bearer"} if error.status_code == 401 else None
    return HTTPException(
        status_code=error.status_code, detail={"codigo": error.codigo, "mensaje": error.mensaje}, headers=headers
    )


def http_desde_error_dominio(error: ErrorDominioAdmin) -> HTTPException:
    """404 no existe, 409 estado incompatible, 422 datos inválidos (subclases de ValueError), 400 el resto."""
    if isinstance(error, TenantNoEncontrado):
        estado, codigo = 404, "tenant_no_encontrado"
    elif isinstance(error, PurgaEnCurso):  # antes que TransicionInvalida (es su subclase)
        estado, codigo = 409, "purga_en_curso"
    elif isinstance(error, ReduccionSinConfirmar):
        estado, codigo = 409, "confirmacion_reduccion_requerida"
    elif isinstance(error, TransicionInvalida):
        estado, codigo = 409, "transicion_invalida"
    elif isinstance(error, SlugEnUso):
        estado, codigo = 409, "slug_en_uso"
    elif isinstance(error, BotYaConectado):
        estado, codigo = 409, "bot_ya_conectado"
    elif isinstance(error, InvitacionNoPendiente):
        estado, codigo = 409, "invitacion_no_pendiente"
    elif isinstance(error, ValueError):
        estado, codigo = 422, "datos_invalidos"
    else:
        estado, codigo = 400, "solicitud_invalida"
    return HTTPException(status_code=estado, detail={"codigo": codigo, "mensaje": str(error)})
