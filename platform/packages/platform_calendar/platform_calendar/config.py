"""Configuración OAuth del proyecto Google, siempre desde variables de entorno (nunca en el repo)."""

import os
from dataclasses import dataclass, field

from platform_calendar.errores import ErrorConfiguracionCalendar

VARIABLES = (
    "GOOGLE_OAUTH_CLIENT_ID",
    "GOOGLE_OAUTH_CLIENT_SECRET",
    "GOOGLE_OAUTH_REDIRECT_URI",
)
VARIABLE_STATE = "CALENDAR_OAUTH_STATE_SECRET"
MINIMO_STATE = 16


@dataclass(frozen=True)
class ConfigGoogle:
    client_id: str
    client_secret: str = field(repr=False)
    redirect_uri: str
    # Clave HMAC con la que se firma el `state` del flujo OAuth. Solo la necesita quien inicia o completa una
    # autorización (el dashboard); quien solo usa refresh tokens (bot, job de purga) no debe tenerla.
    state_secret: bytes | None = field(default=None, repr=False)

    @classmethod
    def opcional_desde_entorno(cls, *, requiere_state: bool = True) -> "ConfigGoogle | None":
        """Config de Google para decidirla UNA vez al arrancar: `None` si no hay ninguna variable (Calendar apagado).

        Con alguna variable cargada pero no todas, o con la clave del `state` demasiado corta, lanza
        `ErrorConfiguracionCalendar` que nombra la variable (nunca su valor): el servicio no debe arrancar a medias.
        """
        if not any(os.environ.get(v) for v in (*VARIABLES, VARIABLE_STATE)):
            return None
        return cls.desde_entorno(requiere_state=requiere_state)

    @classmethod
    def desde_entorno(cls, *, requiere_state: bool = True) -> "ConfigGoogle":
        """Lee la configuración del entorno.

        Con `requiere_state=True` (por defecto) también exige `CALENDAR_OAUTH_STATE_SECRET`. Con `False` no la
        pide: si no está, `state_secret` queda en None y iniciar o completar una autorización falla.
        """
        variables = VARIABLES + ((VARIABLE_STATE,) if requiere_state else ())
        faltan = [v for v in variables if not os.environ.get(v)]
        if faltan:
            raise ErrorConfiguracionCalendar("faltan variables de entorno: " + ", ".join(faltan))
        clave = os.environ.get(VARIABLE_STATE, "").encode() or None
        if clave is not None and len(clave) < MINIMO_STATE:
            raise ErrorConfiguracionCalendar(f"{VARIABLE_STATE} es demasiado corta (mínimo {MINIMO_STATE} caracteres)")
        return cls(
            client_id=os.environ["GOOGLE_OAUTH_CLIENT_ID"],
            client_secret=os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
            redirect_uri=os.environ["GOOGLE_OAUTH_REDIRECT_URI"],
            state_secret=clave,
        )
