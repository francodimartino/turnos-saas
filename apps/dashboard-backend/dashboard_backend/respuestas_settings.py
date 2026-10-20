"""Modelos de respuesta de los settings del local: lo que sale por HTTP."""

from typing import Any

from pydantic import BaseModel


class SettingsOut(BaseModel):
    # Datos que cada local define para su bot y sus límites de reserva: más allá de las claves conocidas que se validan
    # al escribir, su forma no se puede fijar de antemano, por eso `Any`.
    settings: dict[str, Any]
