"""Modelos de respuesta de la conexión con Google Calendar: lo que sale por HTTP.

Ninguno trae el `code`, el `state` ni un token: la conexión se guarda en la base y la respuesta solo la describe.
"""

from pydantic import BaseModel

from dashboard_backend.respuestas_comunes import FechaHora


class UrlConexionOut(BaseModel):
    """Dirección de Google a la que el navegador manda al negocio a conceder el acceso."""

    url: str


class ResultadoCallbackOut(BaseModel):
    resultado: str
    profesional_id: str


class EstadoCalendarOut(BaseModel):
    profesional_id: str
    conectado: bool
    conectado_desde: FechaHora | None
    google_configurado: bool


class DesconexionOut(BaseModel):
    profesional_id: str
    conectado: bool
    habia_conexion: bool
