"""Reglas compartidas de `tenants.settings` (sin framework ni base)."""

import json
from uuid import UUID

import pytest

from turnos_dominio.settings_tenant import (
    MAX_BYTES_SETTINGS,
    MAX_CLAVES_SETTINGS,
    validar_ajustes_del_alta,
    validar_settings,
)
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


TENANT = UUID("11111111-2222-3333-4444-555555555555")
OTRO_TENANT = UUID("99999999-2222-3333-4444-555555555555")


@pytest.mark.parametrize("plazo", [0, 24, 720])
def test_plazo_de_cancelacion_valido_en_los_bordes(plazo):
    validar_settings({"cancelacion_plazo_horas": plazo})


@pytest.mark.parametrize("plazo", [-1, 721, 24.5, "24", True, False, [24], {"a": 1}])
def test_plazo_de_cancelacion_invalido(plazo):
    with pytest.raises(ValueError, match="cancelacion_plazo_horas"):
        validar_settings({"cancelacion_plazo_horas": plazo})


@pytest.mark.parametrize("clave", ["color_principal", "color_secundario"])
@pytest.mark.parametrize("color", ["#000000", "#FFFFFF", "#a1B2c3"])
def test_colores_validos(clave, color):
    validar_settings({clave: color})


@pytest.mark.parametrize("clave", ["color_principal", "color_secundario"])
@pytest.mark.parametrize(
    "color",
    [
        "000000",
        "#FFF",
        "#12345",
        "#1234567",
        "#GGGGGG",
        "#12345678",
        "<script>alert(1)</script>",
        "red",
        "",
        "#112233\n",
        " #112233",
        123456,
        True,
        ["#112233"],
    ],
)
def test_colores_invalidos(clave, color):
    with pytest.raises(ValueError, match=clave):
        validar_settings({clave: color})


@pytest.mark.parametrize("extension", ["png", "jpg", "webp"])
def test_logo_propio_valido(extension):
    ruta = f"{TENANT}/logo.{extension}"
    validar_settings({"logo_ruta": ruta}, TENANT)
    validar_settings({"logo_ruta": ruta})  # sin el id del tenant se exige la forma


@pytest.mark.parametrize(
    "ruta",
    [
        f"{TENANT}/logo.svg",
        f"{TENANT}/logo.gif",
        f"{TENANT}/logo.PNG",
        f"{TENANT}/../logo.png",
        f"{TENANT}/x/../logo.png",
        f"{TENANT}/sub/logo.png",
        f"/{TENANT}/logo.png",
        f"{TENANT}/logo.png/",
        f"{TENANT}/logo.png\n",
        f"{TENANT}/logo.png ",
        f"{TENANT}/otro.png",
        f"http://{TENANT}/logo.png",
        "https://x.com/logo.png",
        "logo.png",
        "",
        f"{OTRO_TENANT}/logo.png",
        f"{OTRO_TENANT}/../{TENANT}/logo.png",
        f"{TENANT}\\logo.png",
        123,
        True,
        [f"{TENANT}/logo.png"],
    ],
)
def test_logo_invalido_con_el_tenant(ruta):
    with pytest.raises(ValueError, match="logo_ruta"):
        validar_settings({"logo_ruta": ruta}, TENANT)


def test_logo_de_otro_tenant_pasa_la_forma_pero_no_el_tenant():
    ruta = f"{OTRO_TENANT}/logo.png"
    validar_settings({"logo_ruta": ruta})
    with pytest.raises(ValueError, match="logo_ruta"):
        validar_settings({"logo_ruta": ruta}, TENANT)


def test_logo_con_barras_o_esquema_es_invalido_sin_el_tenant():
    for ruta in ("../logo.png", "http://x/logo.png", f"{TENANT}/../logo.png", "/logo.png"):
        with pytest.raises(ValueError, match="logo_ruta"):
            validar_settings({"logo_ruta": ruta})


def test_borrar_las_claves_nuevas_con_null_es_valido():
    for clave in ("cancelacion_plazo_horas", "color_principal", "color_secundario", "logo_ruta"):
        validar_settings({clave: None})


def test_un_settings_anterior_sigue_validando():
    validar_settings(
        {
            "booking_approval_timeout_hours": 24,
            "booking_max_active_per_customer": 5,
            "booking_max_per_hour": 3,
            "booking_min_notice_minutes": 30,
            "local_direccion": "Calle 1",
            "local_horarios": ["Lun a Vie 9 a 18"],
        }
    )


@pytest.mark.parametrize(
    "settings",
    [
        {"logo_ruta": f"{TENANT}/logo.png"},
        {"color_principal": None},
        {"color_secundario": None},
        {"cancelacion_plazo_horas": None},
        {"booking_max_per_hour": None},
    ],
)
def test_alta_rechaza_el_logo_y_el_null_en_claves_que_valida_la_base(settings):
    with pytest.raises(ValueError, match=next(iter(settings))):
        validar_ajustes_del_alta(settings)


def test_alta_acepta_un_settings_valido_y_null_en_claves_libres():
    validar_ajustes_del_alta({"color_principal": "#112233", "cancelacion_plazo_horas": 0, "local_direccion": None})
