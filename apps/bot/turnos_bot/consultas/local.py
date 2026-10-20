"""Datos del local que el bot muestra, leídos de `tenants.settings` (ARCHITECTURE.md §2.6, §2.10).

Claves de `tenants.settings` que usa el bot (las carga el dueño del local desde el dashboard con
`PATCH /local/settings`; todas son opcionales):

- `local_direccion`: texto. Ej. "Av. Siempreviva 742, Springfield".
- `local_horarios`: texto, o lista de textos (uno por línea). Ej. ["Lunes a viernes de 9 a 19", "Sábados de 9 a 13"].
- `local_contacto`: texto libre para comunicarse (teléfono, WhatsApp, email). Ej. "WhatsApp +54 11 5555-0000".

Cualquier otro tipo de valor (número, objeto, vacío) se ignora como si la clave no estuviera, y los textos largos
se recortan: son datos que carga el negocio y llegan tal cual a un chat.
"""

import json
import uuid
from dataclasses import dataclass
from typing import Any

CLAVE_DIRECCION = "local_direccion"
CLAVE_HORARIOS = "local_horarios"
CLAVE_CONTACTO = "local_contacto"

LARGO_MAXIMO = 300
LINEAS_MAXIMAS = 14


@dataclass(frozen=True)
class DatosLocal:
    nombre: str | None
    direccion: str | None
    horarios: str | None
    contacto: str | None


def _texto(valor: Any) -> str | None:
    if not isinstance(valor, str):
        return None
    limpio = " ".join(valor.split())
    return limpio[:LARGO_MAXIMO] or None


def _horarios(valor: Any) -> str | None:
    if isinstance(valor, list):
        lineas = [t for t in (_texto(v) for v in valor[:LINEAS_MAXIMAS]) if t]
        return "\n".join(lineas) or None
    return _texto(valor)


def _como_dict(valor: Any) -> dict[str, Any]:
    if isinstance(valor, str):
        try:
            valor = json.loads(valor)
        except ValueError:
            return {}
    return valor if isinstance(valor, dict) else {}


async def leer_datos_local(conn: Any, tenant_id: uuid.UUID) -> DatosLocal:
    """Datos del local del tenant. Si la fila no es visible, devuelve todo vacío (el bot lo dice con amabilidad)."""
    fila = await conn.fetchrow("select name, settings from public.tenants where id = $1", tenant_id)
    if fila is None:
        return DatosLocal(None, None, None, None)
    settings = _como_dict(fila["settings"])
    return DatosLocal(
        nombre=_texto(fila["name"]),
        direccion=_texto(settings.get(CLAVE_DIRECCION)),
        horarios=_horarios(settings.get(CLAVE_HORARIOS)),
        contacto=_texto(settings.get(CLAVE_CONTACTO)),
    )
