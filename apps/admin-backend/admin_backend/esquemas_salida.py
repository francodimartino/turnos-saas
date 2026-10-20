"""Modelos de respuesta del admin-backend para panel, impersonación y visores.

Convención: los cuerpos que entran terminan en `In` (viven junto a su ruta) y los que salen terminan en `Out` (viven
acá). Cada `Out` lista las mismas claves, tipos y nulos que antes devolvía la ruta: las filas de `turnos_queries` y los
resultados de `platform_admin` se validan contra ellos al responder.

Formato: las rutas ya estaban anotadas `-> dict[str, Any]`, que FastAPI serializa con el serializador de `Any`:
`Decimal` como texto ("2500.00") y fechas UTC con `Z`. Estos modelos conservan ese formato. El dashboard difiere a
propósito: sus rutas no anotaban el retorno y salían por `jsonable_encoder` (número y `+00:00`), así que allá se
preserva eso.
"""

import datetime
import uuid
from decimal import Decimal

from pydantic import BaseModel, RootModel
from typing_extensions import TypeAliasType

# Valor JSON cualquiera (los `platform_settings` guardan escalares, listas y objetos).
ValorJson = TypeAliasType("ValorJson", "str | int | float | bool | None | list[ValorJson] | dict[str, ValorJson]")


class InvitacionOut(BaseModel):
    """Alta o reenvío de invitación del owner: el tenant y cómo quedó la invitación."""

    tenant_id: str
    estado_invitacion: str


class SettingsPlataformaOut(RootModel[dict[str, ValorJson]]):
    """Todos los `platform_settings` como `{clave: valor}`."""


class SettingEditadoOut(BaseModel):
    """Resultado de editar una clave: valor anterior (`desde`) y nuevo (`hasta`)."""

    key: str
    desde: ValorJson
    hasta: ValorJson


class CadenaAuditOut(BaseModel):
    """Verificación de la cadena de hashes del `audit_log`; `tenant_id` null es la cadena de plataforma."""

    tenant_id: str | None
    integra: bool
    fila_invalida_id: str | None
    fila_invalida_chain_seq: int | None


class DescarteLimpiezaOut(BaseModel):
    tenant_id: str
    event_id: str
    estado: str


class BookingResumenOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    end_customer_id: uuid.UUID
    service_id: uuid.UUID
    professional_id: uuid.UUID
    start_at: datetime.datetime
    end_at: datetime.datetime
    status: str
    deposit_status: str
    booking_pack_id: uuid.UUID | None
    pending_expires_at: datetime.datetime | None
    created_at: datetime.datetime
    customer_name: str | None
    service_name: str
    professional_name: str


class BookingDetalleOut(BookingResumenOut):
    google_calendar_event_id: str | None
    customer_phone: str | None
    service_duration_minutes: int
    service_price: Decimal


class ClienteOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    channel: str | None
    phone: str | None
    name: str | None
    deleted_at: datetime.datetime | None
    created_at: datetime.datetime


class ServicioOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    duration_minutes: int
    price: Decimal
    deposit_amount: Decimal | None
    is_active: bool
    created_at: datetime.datetime


class ProfesionalOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    is_active: bool
    tenant_user_id: uuid.UUID | None
    created_at: datetime.datetime


class PackOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    end_customer_id: uuid.UUID
    service_id: uuid.UUID
    total_sessions: int
    used_sessions: int
    origin: str
    payment_status: str
    expires_at: datetime.datetime | None
    created_at: datetime.datetime
