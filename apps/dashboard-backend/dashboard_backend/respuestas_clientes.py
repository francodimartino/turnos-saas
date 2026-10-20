"""Modelos de respuesta de `/clientes` (ARCHITECTURE.md §7.1): lo que sale por HTTP, sin FastAPI.

Siguen la convención de `respuestas_turnos`: `<Entidad>Out` y `Lista<Entidades>Out`, todos los campos obligatorios.
`tenant_id` no se declara: nunca sale.
"""

from typing import Any

from pydantic import BaseModel

from dashboard_backend.respuestas_comunes import FechaHora


class ClienteListadoOut(BaseModel):
    """Un cliente en `GET /clientes`: la consulta compartida del listado no trae `extra_data`."""

    id: str
    channel: str | None
    phone: str | None
    name: str | None
    deleted_at: FechaHora | None
    created_at: FechaHora | None
    sin_cliente_identificable: bool


class ClienteOut(ClienteListadoOut):
    # Datos personales que el negocio define por tenant (§2.11): su forma no se puede fijar de antemano, por eso `Any`.
    extra_data: dict[str, Any] | None


class ListaClientesOut(BaseModel):
    clientes: list[ClienteListadoOut]
    limite: int
    offset: int


class CalendarioLimpiezaOut(BaseModel):
    """Resultado de limpiar Google Calendar tras anonimizar; `eventos` y `no_limpiados` son null si la etapa falló."""

    estado: str
    eventos: int | None
    no_limpiados: int | None


class AnonimizacionOut(BaseModel):
    id: str
    sin_cliente_identificable: bool
    calendario: CalendarioLimpiezaOut
