"""Normalización a E.164 de teléfonos argentinos (ARCHITECTURE.md §2.8). Números inventados."""

import pytest

from bot_app.identidad.telefono import normalizar_e164

MOVIL = "+5491100000000"


@pytest.mark.parametrize(
    "entrada",
    [
        "+54 9 11 0000-0000",  # E.164 con formato
        "+5491100000000",
        "5491100000000",  # Telegram suele mandar el número sin '+'
        "541100000000",  # internacional sin el 9 de móviles: se asume móvil
        "0054 9 11 0000 0000",  # prefijo internacional 00
        "011 15 0000-0000",  # local con 0 y 15
        "(011) 15-0000-0000",
        "11 15 0000 0000",
        "11 0000 0000",  # área + número
        "01100000000",  # 0 + área + número
        "  +54-9-11-0000-0000  ",
        "9 11 0000 0000",  # nacional con el 9
    ],
)
def test_formas_de_un_movil_de_buenos_aires_dan_el_mismo_e164(entrada):
    assert normalizar_e164(entrada) == MOVIL


def test_codigo_de_area_de_tres_digitos_con_15():
    assert normalizar_e164("0341 15 000 0000") == "+5493410000000"
    assert normalizar_e164("341 000 0000") == "+5493410000000"


def test_codigo_de_area_de_cuatro_digitos_con_15():
    # 4 dígitos de área + 15 + 6 dígitos de abonado
    assert normalizar_e164("02966 15 000000") == "+5492966000000"


def test_numero_de_otro_pais_en_formato_e164_se_conserva():
    assert normalizar_e164("+34 600 000 000") == "+34600000000"
    assert normalizar_e164("0034600000000") == "+34600000000"


@pytest.mark.parametrize(
    "entrada",
    [
        "",
        "   ",
        "abc",
        "11-ABCD-0000",
        "123",
        "+54 9 11 123",  # corto
        "+54 9 11 0000 0000 0",  # largo
        "5 0000 0000",
        "+999",
        "+" + "1" * 16,  # más de 15 dígitos
        None,
        1100000000,
    ],
)
def test_entradas_invalidas_devuelven_none(entrada):
    assert normalizar_e164(entrada) is None


def test_es_idempotente():
    assert normalizar_e164(normalizar_e164("011 15 0000-0000")) == MOVIL
