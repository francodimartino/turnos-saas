"""Cuerpos de entrada de `/clientes`: validación de forma, sin reglas de negocio ni FastAPI."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator
from turnos_dominio.valores_json import contiene_no_finito


def _sin_no_finitos(valor: dict[str, Any] | None) -> dict[str, Any] | None:
    """Infinity/NaN, aun anidados, no entran en jsonb: sin esto terminaban en un 500 al insertar."""
    if valor is not None and contiene_no_finito(valor):
        raise ValueError("extra_data no admite valores no finitos (Infinity, NaN)")
    return valor


class AltaCliente(BaseModel):
    # `extra="ignore"`: un `tenant_id` en el body se descarta; el tenant sale de la sesión.
    model_config = ConfigDict(extra="ignore")

    phone: str = Field(max_length=64)
    name: str = Field(max_length=200)
    extra_data: dict[str, Any] = Field(default_factory=dict)

    _finito = field_validator("extra_data")(_sin_no_finitos)


class EdicionCliente(BaseModel):
    model_config = ConfigDict(extra="ignore")

    phone: str | None = Field(default=None, max_length=64)
    name: str | None = Field(default=None, max_length=200)
    extra_data: dict[str, Any] | None = None

    _finito = field_validator("extra_data")(_sin_no_finitos)
