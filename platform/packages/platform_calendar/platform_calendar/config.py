"""Configuración OAuth del proyecto Google, siempre desde variables de entorno (nunca en el repo)."""

import os
from dataclasses import dataclass, field

from platform_calendar.errores import ErrorConfiguracionCalendar

VARIABLES = (
    "GOOGLE_OAUTH_CLIENT_ID",
    "GOOGLE_OAUTH_CLIENT_SECRET",
    "GOOGLE_OAUTH_REDIRECT_URI",
    "CALENDAR_OAUTH_STATE_SECRET",
)


@dataclass(frozen=True)
class ConfigGoogle:
    client_id: str
    client_secret: str = field(repr=False)
    redirect_uri: str
    state_secret: bytes = field(repr=False)  # clave HMAC con la que se firma el `state` del flujo OAuth

    @classmethod
    def desde_entorno(cls) -> "ConfigGoogle":
        faltan = [v for v in VARIABLES if not os.environ.get(v)]
        if faltan:
            raise ErrorConfiguracionCalendar("faltan variables de entorno: " + ", ".join(faltan))
        clave = os.environ["CALENDAR_OAUTH_STATE_SECRET"].encode()
        if len(clave) < 16:
            raise ErrorConfiguracionCalendar("CALENDAR_OAUTH_STATE_SECRET es demasiado corta (mínimo 16 caracteres)")
        return cls(
            client_id=os.environ["GOOGLE_OAUTH_CLIENT_ID"],
            client_secret=os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
            redirect_uri=os.environ["GOOGLE_OAUTH_REDIRECT_URI"],
            state_secret=clave,
        )
