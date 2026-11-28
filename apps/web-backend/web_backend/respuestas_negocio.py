"""Modelos de respuesta de la web pública: lo que sale por HTTP, sin FastAPI.

Todos los campos son obligatorios (los opcionales van como `null`). No sale el id del negocio: solo los ids del
catálogo que hacen falta para pedir los horarios libres.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, PlainSerializer, WithJsonSchema


def _numero(valor: Decimal) -> int | float:
    """Entero si no tiene decimales declarados (un precio de 1500), flotante si los tiene (1500.50)."""
    return int(valor) if valor.as_tuple().exponent >= 0 else float(valor)  # type: ignore[operator]


Numero = Annotated[
    Decimal,
    PlainSerializer(_numero, return_type=int | float, when_used="json"),
    WithJsonSchema({"type": "number"}),
]


class ServicioOut(BaseModel):
    id: uuid.UUID
    nombre: str
    duracion_minutos: int
    precio: Numero
    profesionales: list[uuid.UUID]


class ProfesionalOut(BaseModel):
    id: uuid.UUID
    nombre: str
    servicios: list[uuid.UUID]


class NegocioOut(BaseModel):
    nombre: str
    slug: str
    logo: str | None
    color_principal: str | None
    color_secundario: str | None
    direccion: str | None
    zona_horaria: str
    servicios: list[ServicioOut]
    profesionales: list[ProfesionalOut]


class HuecoOut(BaseModel):
    inicio: datetime
    fin: datetime


class HuecosOut(BaseModel):
    zona_horaria: str
    huecos: list[HuecoOut]
