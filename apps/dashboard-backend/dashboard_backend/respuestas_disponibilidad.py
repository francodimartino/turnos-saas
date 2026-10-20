"""Modelos de respuesta de `/reglas-disponibilidad` y `/bloqueos-disponibilidad`: lo que sale por HTTP, sin FastAPI.

Siguen la convención de `respuestas_turnos`: `<Entidad>Out`, todos los campos obligatorios. Los listados son una lista
JSON de elementos (sin envoltorio), como hasta ahora.
"""

import uuid
from datetime import time

from pydantic import BaseModel

from dashboard_backend.respuestas_comunes import FechaHora


class ReglaOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    professional_id: uuid.UUID
    day_of_week: int
    start_time: time
    end_time: time
    created_at: FechaHora


class BloqueoOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    professional_id: uuid.UUID | None
    start_at: FechaHora
    end_at: FechaHora
    reason: str | None
    created_at: FechaHora
