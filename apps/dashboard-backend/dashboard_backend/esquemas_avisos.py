"""Cuerpo de entrada de `/local/avisos`: validación de forma y topes, sin reglas de negocio ni FastAPI."""

import uuid

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from dashboard_backend.avisos_negocio import MAXIMO_CAMBIOS


class _Entrada(BaseModel):
    model_config = ConfigDict(extra="ignore")


class CambioAvisosCuerpo(_Entrada):
    """Una persona: `por_defecto` (borra su fila) o sus dos preferencias. WhatsApp existe pero solo admite falso."""

    staff_id: uuid.UUID
    por_defecto: StrictBool = False
    recibe_avisos: StrictBool | None = None
    canal_email: StrictBool | None = None
    canal_whatsapp: StrictBool = False


class AvisosCuerpo(_Entrada):
    cambios: list[CambioAvisosCuerpo] = Field(min_length=1, max_length=MAXIMO_CAMBIOS)
