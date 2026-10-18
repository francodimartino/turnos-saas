"""Todo `offset` de los listados del admin tiene cota superior: un valor desmedido es 422, no un 500."""

import asyncio
import uuid

import platform_admin as pa
import pytest

from admin_backend.tests.soporte import app_de_prueba, bearer, http

OFFSET_MAX = 2_147_483_647
# Tope explicito por request: bajo carga puede tardar, pero no se cuelga.
TIMEOUT_SEG = 30


@pytest.fixture(scope="module")
def app_modulo():
    """Una sola app para todo el modulo: armarla (routers, esquema OpenAPI) por caso es lo mas
    caro del test y lo que mas se estira cuando la maquina esta cargada."""
    return app_de_prueba()[0]


def test_todo_offset_declara_un_maximo(app_modulo):
    esquema = app_modulo.openapi()
    encontrados = [
        (ruta, p)
        for ruta, metodos in esquema["paths"].items()
        for operacion in metodos.values()
        for p in operacion.get("parameters", [])
        if p["name"] == "offset"
    ]
    assert encontrados, "no se encontró ningún parámetro offset"
    for ruta, p in encontrados:
        maximo = p["schema"].get("maximum")
        assert maximo is not None and maximo <= OFFSET_MAX, f"{ruta}: offset sin le="


async def test_audit_log_offset_desmedido_es_422(monkeypatch, app_modulo):
    async def listar(pool, tenant_id, **kw):
        return []

    monkeypatch.setattr(pa, "listar_audit_log", listar)
    async with http(app_modulo) as c:
        url = f"/tenants/{uuid.uuid4()}/audit-log?offset="
        assert (
            await asyncio.wait_for(c.get(url + "9223372036854775808", headers=bearer()), TIMEOUT_SEG)
        ).status_code == 422
        assert (await asyncio.wait_for(c.get(url + "10", headers=bearer()), TIMEOUT_SEG)).status_code == 200
