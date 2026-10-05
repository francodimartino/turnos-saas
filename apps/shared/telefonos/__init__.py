"""Normalización de teléfonos a E.164, compartida por dashboard-backend y bot (ARCHITECTURE.md §2.3, §2.8).

`end_customers.phone` se guarda siempre en E.164 (`+` y de 8 a 15 dígitos, el primero distinto de 0): es lo que
permite deduplicar entre el alta manual del tenant y el alta por bot.

Regla deliberadamente estricta: solo se acepta un número con código de país explícito, con `+` o con el prefijo
internacional `00`. Un número "nacional" (por ejemplo `11 5550-0101`) se rechaza en vez de adivinarle el país: en
Argentina, `+54 11 ...` y `+54 9 11 ...` son el mismo teléfono celular visto por canales distintos, y adivinar
generaría duplicados silenciosos. El bot parte de `wa_id`, que ya trae el código de país: lo normaliza como
`normalizar_e164("+" + wa_id)`.

Los mensajes de error nunca incluyen el valor recibido (es PII).
"""

import re

__all__ = ["TelefonoInvalido", "normalizar_e164"]

_SEPARADORES = re.compile(r"[\s\-.()]")
_E164 = re.compile(r"^\+[1-9]\d{7,14}$")


class TelefonoInvalido(ValueError):
    """El valor no es un teléfono E.164 inequívoco. El mensaje no incluye el valor."""


def normalizar_e164(valor: object) -> str:
    """Devuelve `valor` en E.164 (`+5491155500101`) o levanta `TelefonoInvalido`."""
    if not isinstance(valor, str):
        raise TelefonoInvalido("el teléfono debe ser un texto")
    texto = _SEPARADORES.sub("", valor.strip())
    if texto.startswith("00"):
        texto = "+" + texto[2:]
    if not _E164.fullmatch(texto):
        raise TelefonoInvalido("teléfono inválido: se espera código de país explícito (+ o 00) y entre 8 y 15 dígitos")
    return texto
