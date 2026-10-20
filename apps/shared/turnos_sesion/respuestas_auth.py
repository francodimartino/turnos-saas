"""Modelos de respuesta de los `/auth/*` compartidos: lo que sale por HTTP, sin FastAPI.

Ninguno trae un token: el acceso y el refresco viajan solo en cookies HttpOnly. Los campos opcionales se omiten del
JSON cuando no aplican (las rutas devuelven el cuerpo con `response_model_exclude_unset`).
"""

from typing import Literal

from pydantic import BaseModel


class SiguientePasoOut(BaseModel):
    """Respuesta de `/auth/login` y de la aceptación de una invitación: qué sigue y cuándo vence el acceso."""

    siguiente_paso: Literal["verificar_mfa", "enrolar_mfa", "listo"]
    factor_id: str | None = None
    expira_en: int


class MFAVerificadaOut(BaseModel):
    siguiente_paso: Literal["listo"]
    expira_en: int


class EnrolarMFAOut(BaseModel):
    """El factor TOTP recién creado: `secret` y `qr_code` se muestran una sola vez para configurar la app."""

    factor_id: str
    qr_code: str
    secret: str
    uri: str


class RefrescoOut(BaseModel):
    expira_en: int


class SesionOut(BaseModel):
    """Quién soy. Cada backend completa lo suyo: el dashboard suma tenant, rol y permisos; el admin, solo lo común."""

    user_id: str
    aal: str
    tenant_id: str | None = None
    is_owner: bool | None = None
    permisos: list[str] | None = None
