"""Traducción de errores de `platform_admin` a respuestas HTTP (los de `platform_auth` están en `turnos_http`).

Formato único: `{"detail": {"codigo": ..., "mensaje": ...}}`. Los mensajes de dominio son genéricos y nunca
incluyen emails ni credenciales; lo que no sea de estas jerarquías es un fallo real (500 sin detalle).
"""

from fastapi import HTTPException
from platform_admin import (
    BotYaConectado,
    ErrorDominioAdmin,
    InvitacionNoPendiente,
    LimpiezaNoPendiente,
    OwnerNoDisponible,
    PurgaEnCurso,
    ReduccionSinConfirmar,
    SlugEnUso,
    TenantNoEncontrado,
    TransicionInvalida,
)
from turnos_http import error_http


def http_desde_error_dominio(error: ErrorDominioAdmin) -> HTTPException:
    """404 no existe, 409 estado incompatible, 422 datos inválidos (subclases de ValueError), 400 el resto."""
    if isinstance(error, TenantNoEncontrado):
        estado, codigo = 404, "tenant_no_encontrado"
    elif isinstance(error, LimpiezaNoPendiente):
        estado, codigo = 404, "limpieza_no_pendiente"
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
    elif isinstance(error, OwnerNoDisponible):
        estado, codigo = 409, "owner_no_disponible"
    elif isinstance(error, InvitacionNoPendiente):
        estado, codigo = 409, "invitacion_no_pendiente"
    elif isinstance(error, ValueError):
        estado, codigo = 422, "datos_invalidos"
    else:
        estado, codigo = 400, "solicitud_invalida"
    return error_http(estado, codigo, str(error))
