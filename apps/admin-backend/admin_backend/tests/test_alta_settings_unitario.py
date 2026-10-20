"""El alta de tenant valida `settings` con las mismas reglas que el PATCH del dashboard, antes de tocar la base."""

import json
import uuid

import platform_admin as pa
import pytest
from turnos_dominio.settings_tenant import MAX_BYTES_SETTINGS

from admin_backend.tests.soporte import app_de_prueba, bearer, http

TENANT = uuid.uuid4()
BASE = {"name": "Sol", "slug": "sol", "owner_email": "dueña@ejemplo.test", "owner_nombre": "Lucía Gómez"}


@pytest.fixture
def altas(monkeypatch):
    recibidos = []

    async def create_tenant(pool, cliente, tenant_data, owner_email, *, owner_nombre, actor_id):
        recibidos.append(tenant_data)
        return pa.ResultadoAlta(tenant_id=TENANT, owner_id=uuid.uuid4(), estado=pa.INVITE_OK)

    monkeypatch.setattr(pa, "create_tenant", create_tenant)
    return recibidos


async def _alta(settings):
    app, _, _ = app_de_prueba()
    async with http(app) as c:
        return await c.post("/tenants", json={**BASE, "settings": settings}, headers=bearer())


@pytest.mark.parametrize(
    "settings",
    [
        {},
        {"local_direccion": "Calle 1", "booking_max_per_hour": 60},
        {"booking_min_notice_minutes": 0},
        {"clave_desconocida": {"a": [1, 2]}},
    ],
)
async def test_alta_con_settings_validos_sigue_funcionando(altas, settings):
    r = await _alta(settings)
    assert r.status_code == 201, r.text
    assert altas[0]["settings"] == settings


@pytest.mark.parametrize(
    ("clave", "valor"),
    [
        ("booking_max_per_hour", 61),
        ("booking_min_notice_minutes", -1),
        ("booking_approval_timeout_hours", "12"),
        ("local_direccion", "x" * 501),
        ("end_customer_extra_fields", "alergias"),
    ],
)
async def test_alta_con_valor_fuera_de_rango_es_422_que_nombra_la_clave(altas, clave, valor):
    r = await _alta({clave: valor})
    assert r.status_code == 422, r.text
    assert clave in r.text
    assert altas == []


async def test_alta_con_mas_de_50_claves_es_422(altas):
    assert (await _alta({f"k{i}": 1 for i in range(50)})).status_code == 201
    assert (await _alta({f"k{i}": 1 for i in range(51)})).status_code == 422
    assert len(altas) == 1


async def test_alta_con_clave_larga_o_vacia_es_422(altas):
    assert (await _alta({"k" * 65: 1})).status_code == 422
    assert (await _alta({"  ": 1})).status_code == 422
    assert altas == []


async def test_alta_con_no_finitos_es_422(altas):
    app, _, _ = app_de_prueba()
    cuerpo = json.dumps(BASE)[:-1] + ', "settings": {"x": [Infinity]}}'
    async with http(app) as c:
        r = await c.post("/tenants", content=cuerpo, headers={**bearer(), "content-type": "application/json"})
    assert r.status_code == 422
    assert altas == []


def _con_bytes(total):
    """Settings cuyo JSON mide exactamente `total` caracteres."""
    vacio = len(json.dumps({"k": ""}))
    return {"k": "x" * (total - vacio)}


async def test_el_tope_de_bytes_se_prueba_en_el_borde_exacto(altas):
    justo = _con_bytes(MAX_BYTES_SETTINGS)
    assert len(json.dumps(justo)) == MAX_BYTES_SETTINGS
    assert (await _alta(justo)).status_code == 201
    pasado = _con_bytes(MAX_BYTES_SETTINGS + 1)
    r = await _alta(pasado)
    assert r.status_code == 422
    assert len(altas) == 1
