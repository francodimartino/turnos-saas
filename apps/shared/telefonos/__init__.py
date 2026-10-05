"""Normalización de teléfonos a E.164, única para dashboard-backend y bot (ARCHITECTURE.md §2.3, §2.8).

`end_customers.phone` se guarda siempre en E.164 para poder deduplicar el alta manual del tenant contra el alta
por bot. Hay una sola función (`normalizar_e164`) y la usan los dos: si cada uno normalizara a su manera, el mismo
teléfono quedaría guardado de dos formas y la deduplicación fallaría en silencio. Reglas (mercado del piloto:
Argentina):

- Con `+` o con prefijo internacional `00`, el número se toma como viene: `+<código de país><número>`, entre 8 y
  15 dígitos (límite del plan E.164, el primero distinto de 0). Los de otro país se conservan tal cual.
- Sin `+` ni `00` se asume Argentina. Telegram suele entregar el número sin `+` (`5491100000000`): si empieza
  con `54` y tiene 12 o 13 dígitos se lo trata como internacional (ningún código de área argentino empieza con 5).
- Todo número argentino queda como celular: `+549` + área (2 a 4 dígitos) + abonado, 10 dígitos después del `9`.
  Se aceptan el `0` de troncal (`011...`), el `15` entre el área y el abonado (`011 15 0000-0000`,
  `0341 15 ...`, `02966 15 ...`), el `9` ya presente y su ausencia (`541100000000`: se asume celular, porque
  `+54 11 ...` y `+54 9 11 ...` son el mismo teléfono visto por canales distintos).
- Separadores permitidos: espacios, guiones, puntos y paréntesis. Cualquier otro carácter o un largo que no
  cierre es inválido (nunca se adivina).

Es idempotente. Los mensajes de error nunca incluyen el valor recibido (es PII).
"""

import re

__all__ = ["TelefonoInvalido", "normalizar_e164"]

_PERMITIDO = re.compile(r"^\+?[\d\s().\-]+$")
_SEPARADORES = re.compile(r"[\s().\-+]")
_MIN_DIGITOS, _MAX_DIGITOS = 8, 15
_MENSAJE = "teléfono inválido: se espera un número de 8 a 15 dígitos; sin + ni 00 se asume Argentina"


class TelefonoInvalido(ValueError):
    """El valor no se puede interpretar como un teléfono E.164 inequívoco. El mensaje no incluye el valor."""


def _celular_argentino(n: str) -> str | None:
    """`n` es el número nacional (sin el `54`). Devuelve el E.164 celular o `None`."""
    if n.startswith("0"):
        n = n[1:]  # el 0 de troncal
    if len(n) == 12:
        # área + 15 + abonado: el área (2, 3 o 4 dígitos) hace que área + 15 + abonado sumen 12 dígitos.
        candidatas = (2,) if n.startswith("11") else (3, 4)
        for area in candidatas:
            if n[0] in "123" and n[area : area + 2] == "15":
                n = n[:area] + n[area + 2 :]
                break
        else:
            return None
    elif len(n) == 11 and n.startswith("9"):
        n = n[1:]
    if len(n) != 10 or n[0] not in "123":
        return None
    return "+549" + n


def _interpretar(entrada: object) -> str | None:
    if not isinstance(entrada, str):
        return None
    texto = entrada.strip()
    if not texto or not _PERMITIDO.match(texto) or "+" in texto[1:]:
        return None
    digitos = _SEPARADORES.sub("", texto)
    internacional = texto.startswith("+")
    if not internacional and digitos.startswith("00"):
        digitos, internacional = digitos[2:], True
    if not internacional and digitos.startswith("54") and len(digitos) in (12, 13):
        internacional = True
    if internacional:
        if digitos.startswith("54"):
            return _celular_argentino(digitos[2:])
        if not (_MIN_DIGITOS <= len(digitos) <= _MAX_DIGITOS) or digitos.startswith("0"):
            return None
        return "+" + digitos
    return _celular_argentino(digitos)


def normalizar_e164(valor: object) -> str:
    """Devuelve `valor` en E.164 (`+5491155500101` para celulares argentinos) o levanta `TelefonoInvalido`."""
    resultado = _interpretar(valor)
    if resultado is None:
        raise TelefonoInvalido(_MENSAJE)
    return resultado
