"""Normalización de teléfonos a E.164 (ARCHITECTURE.md §2.3, §2.8). Los números son ficticios (rango de ejemplo)."""

import pytest

from telefonos import TelefonoInvalido, normalizar_e164


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("+54 9 11 5550-0101", "+5491155500101"),
        ("+54 (9) 11 5550.0101", "+5491155500101"),
        ("0054 9 11 5550 0101", "+5491155500101"),
        ("  +5491155500101  ", "+5491155500101"),
        ("+1 415 555 0132", "+14155550132"),
        ("+34 612 345 678", "+34612345678"),
    ],
)
def test_normaliza_formatos_internacionales(entrada, esperado):
    assert normalizar_e164(entrada) == esperado


def test_el_wa_id_del_bot_se_normaliza_anteponiendo_el_mas():
    # El bot recibe `wa_id` sin '+': lo normaliza anteponiéndolo explícitamente.
    assert normalizar_e164("+" + "5491155500101") == "+5491155500101"


@pytest.mark.parametrize(
    "entrada",
    [
        "",
        "   ",
        "11 5550-0101",  # sin código de país: ambiguo, no se adivina
        "+0 123 456 789",  # el código de país no empieza en 0
        "+54 11",  # muy corto
        "+54911555001011234",  # más de 15 dígitos
        "+54 9 11 ABCD 0101",
        "+54 9 11 5550 0101 ext 5",
        "++5491155500101",
        "5491155500101",  # sin '+' ni '00'
    ],
)
def test_rechaza_lo_que_no_es_e164_inequivoco(entrada):
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
    una = normalizar_e164("+54 9 11 5550-0101")
    assert normalizar_e164(una) == una
