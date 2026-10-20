"""Bordes de dígitos del filtro de teléfonos y forma exacta del texto redactado (ARCHITECTURE.md §6.1)."""

import pytest

from turnos_observabilidad.pii import MARCA_FILTRADO, limpiar_texto

F = MARCA_FILTRADO


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        # Sin "+": se filtra de 10 a 13 dígitos.
        ("tel 123456789 fin", "tel 123456789 fin"),  # 9
        ("tel 1234567890 fin", f"tel {F} fin"),  # 10
        ("tel 1234567890123 fin", f"tel {F} fin"),  # 13
        ("tel 12345678901234 fin", "tel 12345678901234 fin"),  # 14
        # Con separadores.
        ("tel 12345 6789 fin", "tel 12345 6789 fin"),  # 9
        ("tel 12345 67890 fin", f"tel {F} fin"),  # 10
        ("tel 123-4567-890123 fin", f"tel {F} fin"),  # 13
        ("tel 1234-5678-901234 fin", "tel 1234-5678-901234 fin"),  # 14
        # Con "+": se filtra de 10 a 15 dígitos.
        ("tel +123456789 fin", "tel +123456789 fin"),  # 9
        ("tel +1234567890 fin", f"tel {F} fin"),  # 10
        ("tel +123456789012345 fin", f"tel {F} fin"),  # 15
        ("tel +1234567890123456 fin", "tel +1234567890123456 fin"),  # 16
        # Con "+" y separadores.
        ("tel +123 4567 89012345 fin", f"tel {F} fin"),  # 15
        ("tel +123 4567 890123456 fin", "tel +123 4567 890123456 fin"),  # 16
    ],
)
def test_bordes_de_digitos_de_telefonos(entrada, esperado):
    assert limpiar_texto(entrada) == esperado


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        (
            "GET https://api.telegram.org/bot123456:ABC-def_GHI/sendMessage?x=1 fallo",
            f"GET https://api.telegram.org/bot{F}/sendMessage?x=1 fallo",
        ),
        (
            "llamó a /cb?state=ok&access_token=abc123&lang=es y volvió",
            f"llamó a /cb?state=ok&access_token={F}&lang=es y volvió",
        ),
        (
            'respuesta: {"id": 5, "refresh_token": "r-1.2", "ok": true} listo',
            f'respuesta: {{"id": 5, "refresh_token": "{F}", "ok": true}} listo',
        ),
        (
            "Authorization: Bearer abc.def-123 enviado",
            f"Authorization: Bearer {F} enviado",
        ),
        (
            # DSN con usuario y clave (el filtro cubre cualquier esquema; se usa redis de ejemplo).
            "conectando a redis://app:claveFalsa@cache.interno:6379/0 con pool",
            f"conectando a redis://app:{F}@cache.interno:6379/0 con pool",
        ),
        (
            "cliente +54 9 11 5555-0000 pidió turno",
            f"cliente {F} pidió turno",
        ),
        (
            "status_code=200 sin secretos",
            "status_code=200 sin secretos",
        ),
    ],
)
def test_forma_exacta_del_texto_redactado(entrada, esperado):
    assert limpiar_texto(entrada) == esperado
