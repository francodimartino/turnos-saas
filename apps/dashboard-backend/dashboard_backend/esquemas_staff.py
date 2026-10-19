"""Cuerpos de entrada de `/staff`: validación de forma, sin reglas de negocio ni FastAPI."""

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints

Clave = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]


class _Entrada(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Invitacion(_Entrada):
    email: Annotated[str, StringConstraints(max_length=254)]
    # `Any` a propósito: la validación (obligatorio, 1 a 120 caracteres) la hace `platform_auth` y responde 422
    # `nombre_invalido` sin repetir el valor (la validación de pydantic lo devolvería en el error).
    nombre: Any = None
    permission_keys: list[Clave] = Field(default_factory=list, max_length=50)


class PermisosCuerpo(_Entrada):
    permission_keys: list[Clave] = Field(max_length=50)


class OwnerCuerpo(_Entrada):
    is_owner: StrictBool
