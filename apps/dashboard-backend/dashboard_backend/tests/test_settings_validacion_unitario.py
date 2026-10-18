"""Validación por clave de `tenants.settings`, tope acumulado y rechazo de floats no finitos (sin base real)."""

import json

import pytest

from dashboard_backend.clientes import CAMPOS_EXTRA_SETTING, ErrorExtraData, validar_extra_data
from dashboard_backend.tests.soporte import ORIGEN, http, token
from dashboard_backend.tests.soporte_clientes import app_clientes
from dashboard_backend.tests.test_catalogo_unitario import TENANT, USUARIO, _pedir, entorno  # noqa: F401
from dashboard_backend.tests.test_clientes_unitario import TEL, _h

CLAVES_SETTINGS = "local_direccion local_horarios local_contacto"

VALIDOS = [
    {"local_direccion": "Calle 1"},
    {"local_horarios": "Lun a Vie 9 a 18"},
    {"local_horarios": ["Lun a Vie 9 a 18", "Sab 9 a 13"]},
    {"local_contacto": "WhatsApp +54 11 5555-0000"},
    {"booking_approval_timeout_hours": 24},
    {"booking_max_active_per_customer": 50},
    {"booking_max_per_hour": 60},
    {"booking_min_notice_minutes": 0},
    {"booking_min_notice_minutes": 1440},
    {"end_customer_extra_fields": ["alergias", {"key": "cumple", "label": "Cumpleaños"}]},
    {"local_direccion": None},
    {"clave_desconocida": {"a": [1, 2, {"b": "c"}]}},
]

INVALIDOS = [
    ("local_direccion", 123),
    ("local_direccion", "x" * 501),
    ("local_horarios", 5),
    ("local_horarios", ["ok", 3]),
    ("local_horarios", ["x"] * 31),
    ("local_horarios", "x" * 2001),
    ("local_contacto", {"a": 1}),
    ("local_contacto", "x" * 501),
    ("booking_approval_timeout_hours", "12"),
    ("booking_max_active_per_customer", 1.5),
    ("booking_max_active_per_customer", True),
    ("booking_max_active_per_customer", 51),
    ("booking_max_active_per_customer", 0),
    ("booking_max_per_hour", "3"),
    ("booking_max_per_hour", 61),
    ("booking_min_notice_minutes", -1),
    ("booking_min_notice_minutes", 1441),
    ("booking_min_notice_minutes", False),
    ("end_customer_extra_fields", "alergias"),
    ("end_customer_extra_fields", ["x"] * 21),
    ("end_customer_extra_fields", [5]),
    ("end_customer_extra_fields", [{"label": "sin key"}]),
    ("end_customer_extra_fields", [{"key": "k", "label": 3}]),
    ("end_customer_extra_fields", ["x" * 65]),
]


@pytest.mark.parametrize("settings", VALIDOS)
async def test_valores_validos_se_guardan(entorno, settings):  # noqa: F811
    app, conn = entorno
    conn.fila = {"settings": {}}
    r = await _pedir(app, "PATCH", "/local/settings", {"settings": settings})
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("clave,valor", INVALIDOS)
async def test_valor_invalido_es_422_que_nombra_la_clave_y_no_el_valor(entorno, clave, valor):  # noqa: F811
    app, conn = entorno
    conn.fila = {"settings": {}}
    r = await _pedir(app, "PATCH", "/local/settings", {"settings": {clave: valor}})
    assert r.status_code == 422, r.text
    assert clave in r.text
    assert conn.escrituras() == []
    if isinstance(valor, str) and len(valor) > 5:
        assert valor not in r.text


async def test_el_tope_acumulado_se_aplica_sobre_la_mezcla(entorno):  # noqa: F811
    app, conn = entorno
    existente = {f"k{i}": "x" * 400 for i in range(48)}  # ~19 KB
    conn.fila = {"settings": existente}
    antes = json.dumps(existente)
    r = await _pedir(app, "PATCH", "/local/settings", {"settings": {"otra": "y" * 1500}})
    assert r.status_code == 422, r.text
    assert "20" in r.text or "grande" in r.text
    assert conn.escrituras() == []
    assert json.dumps(conn.fila["settings"]) == antes
    assert not any("audit" in s.lower() for s in conn.sentencias())


async def test_un_patch_que_no_supera_el_tope_acumulado_pasa(entorno):  # noqa: F811
    app, conn = entorno
    conn.fila = {"settings": {f"k{i}": "x" * 400 for i in range(48)}}
    r = await _pedir(app, "PATCH", "/local/settings", {"settings": {"k0": None, "k1": "corto"}})
    assert r.status_code == 200, r.text


def _escrituras(conn):
    return [x for x in conn.sentencias() if x.lstrip().lower().startswith(("insert", "update", "delete"))]


def _crudo(app, metodo, ruta, cuerpo, headers):
    return http(app).request(metodo, ruta, content=cuerpo, headers={**headers, "Content-Type": "application/json"})


@pytest.mark.parametrize("literal", ["Infinity", "-Infinity", "NaN"])
@pytest.mark.parametrize(
    "plantilla",
    ['{"settings": {"cualquiera": LIT}}', '{"settings": {"cualquiera": [1, {"x": [LIT]}]}}'],
)
async def test_settings_rechaza_no_finitos_aun_anidados(entorno, literal, plantilla):  # noqa: F811
    app, conn = entorno
    conn.fila = {"settings": {}}
    h = {"Origin": ORIGEN, "Cookie": f"dash_acceso={token(user_id=USUARIO, tenant_id=TENANT)}"}
    r = await _crudo(app, "PATCH", "/local/settings", plantilla.replace("LIT", literal).encode(), h)
    assert r.status_code == 422, r.text
    assert conn.escrituras() == []


@pytest.mark.parametrize("literal", ["Infinity", "-Infinity", "NaN"])
@pytest.mark.parametrize(
    "metodo,ruta,plantilla",
    [
        ("POST", "/clientes", '{"phone": "TEL", "name": "C", "extra_data": {"alergias": LIT}}'.replace("TEL", TEL)),
        (
            "POST",
            "/clientes",
            '{"phone": "TEL", "name": "C", "extra_data": {"alergias": [{"a": [LIT]}]}}'.replace("TEL", TEL),
        ),
    ],
)
async def test_alta_de_cliente_rechaza_no_finitos(literal, metodo, ruta, plantilla):
    app, conn, _ = app_clientes()
    conn.settings = {CAMPOS_EXTRA_SETTING: ["alergias"]}
    r = await _crudo(app, metodo, ruta, plantilla.replace("LIT", literal).encode(), _h(escribe=True))
    assert r.status_code == 422, r.text
    assert _escrituras(conn) == []


@pytest.mark.parametrize("literal", ["Infinity", "-Infinity", "NaN"])
@pytest.mark.parametrize("anidado", [False, True])
async def test_edicion_de_cliente_rechaza_no_finitos(literal, anidado):
    import uuid

    app, conn, _ = app_clientes()
    conn.settings = {CAMPOS_EXTRA_SETTING: ["alergias"]}
    valor = f"[{literal}]" if anidado else literal
    cuerpo = ('{"extra_data": {"alergias": VAL}}'.replace("VAL", valor)).encode()
    r = await _crudo(app, "PATCH", f"/clientes/{uuid.uuid4()}", cuerpo, _h(escribe=True))
    assert r.status_code == 422, r.text
    assert _escrituras(conn) == []


@pytest.mark.parametrize("valor", [float("inf"), float("-inf"), float("nan")])
def test_validar_extra_data_rechaza_floats_no_finitos(valor):
    with pytest.raises(ErrorExtraData):
        validar_extra_data({"alergias": valor}, ["alergias"])


@pytest.mark.parametrize("literal", ["Infinity", "-Infinity", "NaN"])
@pytest.mark.parametrize(
    "metodo,ruta,plantilla",
    [
        ("POST", "/servicios", '{"name": "Corte", "duration_minutes": 30, "price": LIT}'),
        ("POST", "/servicios", '{"name": "Corte", "duration_minutes": 30, "price": 1, "deposit_amount": LIT}'),
    ],
)
async def test_campos_monetarios_rechazan_no_finitos(entorno, literal, metodo, ruta, plantilla):  # noqa: F811
    app, conn = entorno
    h = {"Origin": ORIGEN, "Cookie": f"dash_acceso={token(user_id=USUARIO, tenant_id=TENANT)}"}
    r = await _crudo(app, metodo, ruta, plantilla.replace("LIT", literal).encode(), h)
    assert r.status_code == 422, r.text
    assert conn.escrituras() == []
