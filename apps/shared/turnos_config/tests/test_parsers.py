"""Fija los parsers de configuración de arranque que comparten los backends."""

import ast
from pathlib import Path

import pytest

from turnos_config import (
    ConfigInvalida,
    booleano,
    encabezado_ip,
    entero,
    entero_o_none,
    obligatoria,
    samesite,
    texto_opcional,
    validar_origen,
    validar_url_supabase,
)

PAQUETE = Path(__file__).resolve().parents[1]
PROHIBIDOS = {"turnos_bot", "dashboard_backend", "admin_backend"}


def test_config_invalida_es_value_error():
    assert issubclass(ConfigInvalida, ValueError)


def test_obligatoria_recorta_y_rechaza_vacia_o_ausente():
    assert obligatoria({"X": "  v "}, "X") == "v"
    for entorno in ({}, {"X": ""}, {"X": "   "}):
        with pytest.raises(ConfigInvalida, match="Falta la variable de entorno X"):
            obligatoria(entorno, "X")


def test_texto_opcional():
    assert texto_opcional({"X": " a "}, "X") == "a"
    assert texto_opcional({}, "X") == ""


@pytest.mark.parametrize("valor", ["true", "TRUE", " 1 ", "si", "sí"])
def test_booleano_verdadero(valor):
    assert booleano(valor, "V", False) is True


@pytest.mark.parametrize("valor", ["false", "0", "no", " No "])
def test_booleano_falso(valor):
    assert booleano(valor, "V", True) is False


@pytest.mark.parametrize("valor", [None, "", "  "])
def test_booleano_vacio_usa_defecto(valor):
    assert booleano(valor, "V", True) is True
    assert booleano(valor, "V", False) is False


def test_booleano_invalido():
    with pytest.raises(ConfigInvalida, match="V debe ser true o false"):
        booleano("quizas", "V", True)


def test_entero():
    assert entero("7", "N", 1) == 7
    assert entero(None, "N", 3) == 3
    assert entero("  ", "N", 3) == 3


@pytest.mark.parametrize(
    ("valor", "mensaje"),
    [("abc", "N debe ser un entero"), ("0", "N debe ser positivo"), ("-2", "N debe ser positivo")],
)
def test_entero_invalido(valor, mensaje):
    with pytest.raises(ConfigInvalida, match=mensaje):
        entero(valor, "N", 1)


def test_entero_o_none():
    assert entero_o_none(None, "N") is None
    assert entero_o_none(" ", "N") is None
    assert entero_o_none("0", "N") == 0
    assert entero_o_none("5", "N") == 5


@pytest.mark.parametrize(("valor", "mensaje"), [("x", "N debe ser un entero"), ("-1", "N no puede ser negativo")])
def test_entero_o_none_invalido(valor, mensaje):
    with pytest.raises(ConfigInvalida, match=mensaje):
        entero_o_none(valor, "N")


def test_url_supabase():
    assert validar_url_supabase("https://x.supabase.co/") == "https://x.supabase.co"
    assert validar_url_supabase("http://localhost:54321") == "http://localhost:54321"


@pytest.mark.parametrize(
    ("url", "mensaje"),
    [("http://x.test", "debe ser https"), ("ftp://x.test", "debe ser https"), ("https://", "sin host")],
)
def test_url_supabase_invalida(url, mensaje):
    with pytest.raises(ConfigInvalida, match=mensaje):
        validar_url_supabase(url)


def test_origen_se_normaliza():
    assert validar_origen("https://panel.test/", "O") == "https://panel.test"
    assert validar_origen("http://localhost:3000", "O") == "http://localhost:3000"


@pytest.mark.parametrize(
    "origen",
    ["*", "panel.test", "https://panel.test/ruta", "ftp://x.test", "https://u@panel.test", "https://p.test?q=1"],
)
def test_origen_invalido_nombra_la_variable(origen):
    with pytest.raises(ConfigInvalida, match="MI_ORIGEN debe ser un único origen"):
        validar_origen(origen, "MI_ORIGEN")


def test_samesite():
    assert samesite({}) == "strict"
    assert samesite({"COOKIE_SAMESITE": " LAX "}) == "lax"


@pytest.mark.parametrize("valor", ["none", "otro"])
def test_samesite_rechaza_none(valor):
    with pytest.raises(ConfigInvalida, match="COOKIE_SAMESITE debe ser 'strict' o 'lax'"):
        samesite({"COOKIE_SAMESITE": valor})


def test_encabezado_ip():
    assert encabezado_ip({}) == ""
    assert encabezado_ip({"LOGIN_IP_HEADER": " CF-Connecting-IP "}) == "cf-connecting-ip"
    assert encabezado_ip({"LOGIN_IP_HEADER": "X-Forwarded-For"}) == "x-forwarded-for"


def test_encabezado_ip_invalido():
    with pytest.raises(ConfigInvalida, match="LOGIN_IP_HEADER debe ser CF-Connecting-IP o X-Forwarded-For"):
        encabezado_ip({"LOGIN_IP_HEADER": "X-Real-IP"})


def _modulos_importados(arbol):
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            yield from (a.name for a in nodo.names)
        elif isinstance(nodo, ast.ImportFrom):
            yield nodo.module or ""


def test_el_paquete_no_importa_codigo_de_las_apps():
    for archivo in PAQUETE.glob("*.py"):
        arbol = ast.parse(archivo.read_text(encoding="utf-8"))
        assert not {m.split(".")[0] for m in _modulos_importados(arbol)} & PROHIBIDOS, archivo.name
