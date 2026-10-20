"""Reglas compartidas de `tenants.settings` (sin framework ni base)."""

import json

import pytest

from turnos_dominio.settings_tenant import MAX_BYTES_SETTINGS, MAX_CLAVES_SETTINGS, validar_settings
from turnos_dominio.valores_json import contiene_no_finito


def test_settings_validos_no_levantan():
    validar_settings({"local_direccion": "Calle 1", "booking_max_per_hour": 60, "otra": {"a": [1]}})


@pytest.mark.parametrize(
    "settings",
    [
        {"booking_max_per_hour": 61},
        {"local_contacto": 5},
        {"x" * 65: 1},
        {" ": 1},
        {"k": float("inf")},
        {"k": [{"a": float("nan")}]},
        {f"k{i}": 1 for i in range(MAX_CLAVES_SETTINGS + 1)},
    ],
)
def test_settings_invalidos_levantan_value_error(settings):
    with pytest.raises(ValueError):  # noqa: PT011
        validar_settings(settings)


def test_tope_de_bytes_en_el_borde():
    vacio = len(json.dumps({"k": ""}))
    validar_settings({"k": "x" * (MAX_BYTES_SETTINGS - vacio)})
    with pytest.raises(ValueError, match="demasiado grandes"):
        validar_settings({"k": "x" * (MAX_BYTES_SETTINGS - vacio + 1)})


def test_contiene_no_finito():
    assert contiene_no_finito({"a": [1, float("-inf")]})
    assert not contiene_no_finito({"a": [1, 2.5, "x"]})
