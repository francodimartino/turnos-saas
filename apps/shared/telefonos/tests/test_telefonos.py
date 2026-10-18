"""Normalización de teléfonos a E.164 (ARCHITECTURE.md §2.3, §2.8). Números ficticios (rango de ejemplo)."""

import re

import pytest

from telefonos import TelefonoInvalido, normalizar_e164

MOVIL = "+5491155500101"


@pytest.mark.parametrize(
    "entrada",
    [
        "+54 9 11 5550-0101",  # E.164 con formato
        "+5491155500101",
        "+54 (9) 11 5550.0101",
        "5491155500101",  # Telegram suele mandar el número sin '+'
        "541155500101",  # internacional sin el 9 de móviles: se asume móvil
        "+54 11 5550-0101",  # sin el 9
        "0054 9 11 5550 0101",  # prefijo internacional 00
        "011 15-5550-0101",  # local con 0 y 15
        "(011) 15 5550 0101",
        "11 15 5550 0101",
        "11 5550 0101",  # área + número
        "01155500101",  # 0 + área + número
        "  +54-9-11-5550-0101  ",
        "9 11 5550 0101",  # nacional con el 9
    ],
)
def test_todas_las_formas_de_un_movil_de_buenos_aires_dan_el_mismo_e164(entrada):
    assert normalizar_e164(entrada) == MOVIL


def test_alta_manual_y_contacto_de_telegram_normalizan_al_mismo_valor():
    manual = [normalizar_e164("011 15-5550-0101"), normalizar_e164("+54 11 5550-0101")]
    telegram = normalizar_e164("5491155500101")
    assert manual == [telegram, telegram] == [MOVIL, MOVIL]


def test_codigo_de_area_de_tres_digitos():
    assert normalizar_e164("0341 15 555 0101") == "+5493415550101"
    assert normalizar_e164("341 555 0101") == "+5493415550101"


def test_codigo_de_area_de_cuatro_digitos_con_15():
    assert normalizar_e164("02966 15 555010") == "+5492966555010"


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("+1 415 555 0132", "+14155550132"),
        ("+34 612 345 678", "+34612345678"),
        ("0034612345678", "+34612345678"),
    ],
)
def test_numeros_de_otro_pais_con_mas_o_00_se_conservan(entrada, esperado):
    assert normalizar_e164(entrada) == esperado


@pytest.mark.parametrize(
    "entrada",
    [
        "",
        "   ",
        "abc",
        "11-ABCD-0101",
        "123",
        "+54 9 11 123",  # corto
        "+54 9 11 5550 0101 0",  # largo
        "5 5550 0101",
        "+999",
        "+0 123 456 789",  # el código de país no empieza en 0
        "+" + "1" * 16,  # más de 15 dígitos
        "+54 9 11 5550 0101 ext 5",
        "++5491155500101",
    ],
)
def test_lo_ambiguo_o_invalido_levanta_telefono_invalido(entrada):
    with pytest.raises(TelefonoInvalido):
        normalizar_e164(entrada)


@pytest.mark.parametrize("entrada", [None, 5491155500101, ["+5491155500101"]])
def test_rechaza_tipos_que_no_son_texto(entrada):
    with pytest.raises(TelefonoInvalido):
        normalizar_e164(entrada)


def test_el_mensaje_de_error_no_incluye_el_valor_recibido():
    with pytest.raises(TelefonoInvalido) as e:
        normalizar_e164("+54 9 11 ZZZZ 0101")
    assert "ZZZZ" not in str(e.value)
    assert "0101" not in str(e.value)


def test_es_idempotente():
    assert normalizar_e164(normalizar_e164("011 15-5550-0101")) == MOVIL


@pytest.mark.parametrize(
    "entrada",
    [
        "+٥٤ ٩ ١١ ٥٥٥٠ ٠١٠١",  # árabe-índico
        "+५४ ९ ११ ५५५० ०१०१",  # devanagari
        "+５４９１１５５５００１０１",  # ancho completo
        "+54 9 11 5550-٠١٠١",  # mezcla: cuerpo ASCII y cola árabe
        "٠١١ ١٥ ٥٥٥٠ ٠١٠١",  # cero árabe (U+0660) en el local
        "+54 9 11 5550\u00a00101",  # separador no ASCII (espacio de no separación)
    ],
)
def test_rechaza_digitos_unicode_no_ascii(entrada):
    with pytest.raises(TelefonoInvalido):
        normalizar_e164(entrada)


@pytest.mark.parametrize("entrada", ["+54 9 11 5550-0101", "011 15-5550-0101", "5491155500101", "+1 415 555 2671"])
def test_la_salida_solo_contiene_mas_y_digitos_ascii(entrada):
    assert re.fullmatch(r"\+[0-9]+", normalizar_e164(entrada))
