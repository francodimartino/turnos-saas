"""Modelos de respuesta del catálogo del local (servicios, profesionales y su asignación): lo que sale por HTTP.

Siguen la convención de `respuestas_turnos`: `<Entidad>Out`, todos los campos obligatorios. Las listas de servicios y
de profesionales son arreglos JSON a secas.
"""

import uuid

from pydantic import BaseModel

from dashboard_backend.respuestas_comunes import FechaHora, Numero


class ServicioOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    duration_minutes: int
    price: Numero
    deposit_amount: Numero | None
    is_active: bool
    created_at: FechaHora


class ProfesionalOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    is_active: bool
    tenant_user_id: uuid.UUID | None
    created_at: FechaHora


class AsignacionOut(BaseModel):
    """Un servicio que hace un profesional y cómo se confirman sus turnos."""

    professional_id: uuid.UUID
    service_id: uuid.UUID
    confirmation_mode: str
