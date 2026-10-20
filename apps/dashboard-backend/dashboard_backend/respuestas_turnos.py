"""Modelos de respuesta de `/turnos` y `/packs` (ARCHITECTURE.md §7.1): lo que sale por HTTP, sin FastAPI.

Convención de las respuestas del dashboard-backend: los modelos de entrada viven en `esquemas_<área>.py` y los de
salida en `respuestas_<área>.py`; se nombran `<Entidad>Out` (un elemento) y `Lista<Entidades>Out` (un listado con
`limite` y `offset`). El router los declara con `response_model=` y la lógica sigue devolviendo diccionarios: el
modelo valida, ordena y filtra lo que sale. Cada campo es obligatorio (un `None` se declara con `| None`, sin
valor por defecto), así que un campo que falte en la lógica falla en el test y no se pierde en silencio.

Los campos internos (`tenant_id`, canal del cliente, cuenta vinculada del profesional, `vencido`) no se declaran:
el modelo los descarta aunque la fila los traiga.
"""

import uuid

from pydantic import BaseModel

from dashboard_backend.respuestas_comunes import FechaHora, Numero


class TurnoBaseOut(BaseModel):
    """Lo que comparten todas las respuestas que muestran un turno."""

    id: uuid.UUID
    end_customer_id: uuid.UUID
    service_id: uuid.UUID
    professional_id: uuid.UUID
    start_at: FechaHora
    end_at: FechaHora
    status: str
    deposit_status: str
    booking_pack_id: uuid.UUID | None
    pending_expires_at: FechaHora | None
    customer_name: str | None
    service_name: str
    professional_name: str
    sin_cliente_identificable: bool


class TurnoListadoOut(TurnoBaseOut):
    """Un turno dentro de `GET /turnos`."""

    created_at: FechaHora


class TurnoDetalleOut(TurnoBaseOut):
    """`GET /turnos/{id}`: suma el contacto del cliente, el precio y la duración del servicio."""

    created_at: FechaHora
    google_calendar_event_id: str | None
    customer_phone: str | None
    service_duration_minutes: int
    service_price: Numero


class CalendarioOut(BaseModel):
    """Qué pasó con Google Calendar al cambiar el turno (`no_aplica`, `error` o la acción hecha)."""

    estado: str


class TurnoCambiadoOut(TurnoBaseOut):
    """Respuesta de un alta o cambio de estado: el turno ya confirmado más lo ocurrido después del COMMIT."""

    google_calendar_event_id: str | None
    customer_phone: str | None
    calendario: CalendarioOut
    aviso: str


class ListaTurnosOut(BaseModel):
    turnos: list[TurnoListadoOut]
    limite: int
    offset: int


class PackOut(BaseModel):
    id: uuid.UUID
    end_customer_id: uuid.UUID
    service_id: uuid.UUID
    total_sessions: int
    used_sessions: int
    origin: str
    payment_status: str
    expires_at: FechaHora | None
    created_at: FechaHora


class ListaPacksOut(BaseModel):
    packs: list[PackOut]
    limite: int
    offset: int
