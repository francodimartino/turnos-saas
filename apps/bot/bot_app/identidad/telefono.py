"""Normalización de teléfonos a E.164 (ARCHITECTURE.md §2.8).

El `phone` de `end_customers` se guarda siempre en E.164 para poder deduplicar el alta por bot contra la carga
manual del dashboard. Reglas (pensadas para Argentina, el mercado del piloto):

- Con `+` o con prefijo internacional `00`, el número se toma como viene: `+<código de país><número>`,
  entre 8 y 15 dígitos en total (límite del plan E.164). Los de otro país se conservan tal cual.
- Sin `+` ni `00` se asume Argentina. Telegram suele entregar el número sin `+` (`5491100000000`): si empieza
  con `54` y tiene 12 o 13 dígitos se lo trata como internacional (ningún código de área argentino empieza con 5).
- Un móvil argentino queda como `+549` + área (2 a 4 dígitos) + abonado, 10 dígitos en total después del `9`.
  Se aceptan las formas habituales: `0` de troncal (`011...`), el `15` entre el área y el abonado
  (`011 15 0000-0000`, `0341 15 ...`, `02966 15 ...`), el `9` ya presente y la falta del `9`
  (`541100000000`: se asume móvil, porque el bot solo habla con personas desde su celular).
- Separadores permitidos: espacios, guiones, puntos y paréntesis. Cualquier otro carácter, o un largo que no
  cierre, devuelve `None` (nunca se adivina).

Es idempotente: `normalizar_e164(normalizar_e164(x)) == normalizar_e164(x)`.
"""

import re

_PERMITIDO = re.compile(r"^\+?[\d\s().\-]+$")
_SEPARADORES = re.compile(r"[\s().\-+]")
_MIN_DIGITOS, _MAX_DIGITOS = 8, 15


def _movil_argentino(n: str) -> str | None:
    """`n` es el número nacional (sin el `54`). Devuelve el E.164 móvil o `None`."""
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


def normalizar_e164(entrada: object) -> str | None:
    """Teléfono en E.164 (`+549...` para móviles argentinos) o `None` si no se puede interpretar."""
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
            return _movil_argentino(digitos[2:])
        if not (_MIN_DIGITOS <= len(digitos) <= _MAX_DIGITOS) or digitos.startswith("0"):
            return None
        return "+" + digitos
    return _movil_argentino(digitos)
