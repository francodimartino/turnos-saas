"""Todo `offset` de los listados del admin tiene cota superior: un valor desmedido es 422, no un 500."""

import uuid

import platform_admin as pa

from admin_backend.tests.soporte import app_de_prueba, bearer, http

OFFSET_MAX = 2_147_483_647


def test_todo_offset_declara_un_maximo():
    esquema = app_de_prueba()[0].openapi()
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


async def test_audit_log_offset_desmedido_es_422(monkeypatch):
    async def listar(pool, tenant_id, **kw):
        return []

    monkeypatch.setattr(pa, "listar_audit_log", listar)
    app, _, _ = app_de_prueba()
    async with http(app) as c:
        url = f"/tenants/{uuid.uuid4()}/audit-log?offset="
        assert (await c.get(url + "9223372036854775808", headers=bearer())).status_code == 422
        assert (await c.get(url + "10", headers=bearer())).status_code == 200
