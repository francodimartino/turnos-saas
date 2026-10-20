"""Modelos de respuesta de los settings del local: lo que sale por HTTP.

Las claves conocidas (las que valida `turnos_dominio.settings_tenant`) tienen su tipo; cualquier otra clave que el
local haya guardado pasa tal cual como clave extra (`extra="allow"`), así que el JSON es el mismo de siempre. Las
respuestas se arman con `response_model_exclude_unset`: solo salen las claves que el local tiene guardadas.
"""

from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr


class CampoExtraOut(BaseModel):
    """Un campo accesorio de `end_customers` que el local pidió, con su etiqueta si la definió."""

    model_config = ConfigDict(extra="allow")

    key: StrictStr
    label: StrictStr | None = None


class SettingsLocalOut(BaseModel):
    """`tenants.settings`: datos del local, límites de reserva, campos accesorios y lo que sume el local."""

    model_config = ConfigDict(extra="allow")

    local_direccion: StrictStr | None = None
    local_contacto: StrictStr | None = None
    local_horarios: StrictStr | list[StrictStr] | None = None
    booking_approval_timeout_hours: StrictInt | None = None
    booking_max_active_per_customer: StrictInt | None = None
    booking_max_per_hour: StrictInt | None = None
    booking_min_notice_minutes: StrictInt | None = None
    end_customer_extra_fields: list[StrictStr | CampoExtraOut] | None = None


class SettingsOut(BaseModel):
    settings: SettingsLocalOut
