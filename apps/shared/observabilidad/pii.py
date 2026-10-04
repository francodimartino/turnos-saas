"""Filtrado de datos personales para logs y Sentry (ARCHITECTURE.md §6.1 y §6.3; Ley 25.326).

Qué filtra:
- Claves de `PII_CAMPOS` (phone, name, channel_user_id, cuerpos de mensajes...), sin distinguir
  mayúsculas, en cualquier nivel de dicts/listas/tuplas anidados.
- Teléfonos dentro de valores de texto (mensajes, excepciones, URLs, strings sueltos): E.164
  (`+` y 10 a 15 dígitos) y números de 10 a 13 dígitos con o sin separadores (espacio, guion,
  punto, paréntesis), p. ej. `+54 9 11 5555-0000`, `011 5555-0000`, `1155550000`. Se reemplazan
  por `[filtrado]`.

Qué NO filtra:
- Nombres, emails, direcciones u otros datos personales que aparezcan dentro de texto libre bajo
  una clave que no es de PII: no hay forma confiable de detectarlos. La regla es no loguearlos.
- Teléfonos escritos con letras o con menos de 10 dígitos (p. ej. solo número local).
- Números de 10+ dígitos con separadores que parezcan una fecha `AAAA-MM-DD` (se dejan pasar).
- Identificadores opacos (uuid, ids internos): no son PII por sí mismos.
"""

import re
from typing import Any

PII_CAMPOS = frozenset(
    {
        "phone",
        "name",
        "channel_user_id",
        "text",
        "body",
        "message_text",
        "message_body",
        "content",
        "caption",
    }
)

MARCA_FILTRADO = "[filtrado]"

_CANDIDATO = re.compile(r"(?<![\w.])\+?\d[\d\s().-]{6,}\d(?!\w)")
_FECHA = re.compile(r"^\d{4}-\d{2}-\d{2}")


def es_campo_pii(clave: object) -> bool:
    return isinstance(clave, str) and clave.lower() in PII_CAMPOS


def _reemplazo(m: re.Match[str]) -> str:
    s = m.group(0)
    if _FECHA.match(s):
        return s
    digitos = sum(c.isdigit() for c in s)
    limite = 15 if s.startswith("+") else 13
    return MARCA_FILTRADO if 10 <= digitos <= limite else s


def limpiar_texto(texto: str) -> str:
    """Reemplaza por `[filtrado]` los teléfonos que aparezcan dentro de `texto`."""
    return _CANDIDATO.sub(_reemplazo, texto)


def limpiar_valor(valor: Any) -> Any:
    """Copia de `valor` sin claves de PII ni teléfonos en textos. Objetos no JSON pasan a `str`."""
    if isinstance(valor, dict):
        return {k: limpiar_valor(v) for k, v in valor.items() if not es_campo_pii(k)}
    if isinstance(valor, list):
        return [limpiar_valor(v) for v in valor]
    if isinstance(valor, tuple):
        return tuple(limpiar_valor(v) for v in valor)
    if isinstance(valor, str):
        return limpiar_texto(valor)
    if valor is None or isinstance(valor, bool | int | float):
        return valor
    return limpiar_texto(str(valor))
