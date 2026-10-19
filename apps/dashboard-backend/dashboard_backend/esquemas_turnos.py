"""Cuerpos de los endpoints de turnos y packs (ARCHITECTURE.md §7.1): modelos de entrada, sin FastAPI.

Todas las fechas exigen zona horaria. Los modelos descartan los campos que no corresponden (`extra="ignore"`): el
tenant sale siempre de la sesión y el estado inicial lo decide el servidor.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_SESIONES_PACK = 1000


def _aware(valor: datetime) -> datetime:
    if valor.tzinfo is None or valor.tzinfo.utcoffset(valor) is None:
        raise ValueError("la fecha debe incluir zona horaria (por ejemplo 2030-03-04T10:00:00-03:00)")
    return valor


class AltaTurno(BaseModel):
    # `extra="ignore"`: un `tenant_id` o un `status` en el body se descartan; el tenant sale de la sesión.
    model_config = ConfigDict(extra="ignore")

    end_customer_id: uuid.UUID
    service_id: uuid.UUID
    professional_id: uuid.UUID
    start_at: datetime
    booking_pack_id: uuid.UUID | None = None

    @field_validator("start_at")
    @classmethod
    def _inicio_con_zona(cls, valor: datetime) -> datetime:
        return _aware(valor)


class MoverTurno(BaseModel):
    model_config = ConfigDict(extra="ignore")

    start_at: datetime

    @field_validator("start_at")
    @classmethod
    def _inicio_con_zona(cls, valor: datetime) -> datetime:
        return _aware(valor)


class AltaPack(BaseModel):
    # `origin` y `payment_status` no se aceptan del cliente: acá siempre es `manual_grant`.
    model_config = ConfigDict(extra="ignore")

    end_customer_id: uuid.UUID
    service_id: uuid.UUID
    total_sessions: int = Field(ge=1, le=MAX_SESIONES_PACK)
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def _vencimiento_con_zona(cls, valor: datetime | None) -> datetime | None:
        return None if valor is None else _aware(valor)
