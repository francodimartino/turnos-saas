"""Verificar cadena de audit_log y descartar limpieza de Calendar: HTTP <-> dominio, sin base real."""

import logging
import uuid

import platform_admin as pa
import pytest

from admin_backend.tests.soporte import app_de_prueba, bearer, http

TENANT = uuid.UUID("00000000-0000-0000-0000-0000000000aa")


async def test_verificar_cadena_de_tenant_integra(monkeypatch):
    visto = {}

    async def verificar(pool, tenant_id, *, actor_id=None):
        visto.update(tenant=tenant_id, actor=actor_id)
        return pa.ResultadoCadena(tenant_id=tenant_id, integra=True)

    monkeypatch.setattr(pa, "verificar_cadena_audit", verificar)
    app, _, _ = app_de_prueba()
    admin_id = uuid.uuid4()
    async with http(app) as c:
        r = await c.get(f"/tenants/{TENANT}/audit-log/verificar-cadena", headers=bearer(user_id=admin_id))
    assert r.status_code == 200
    assert r.json() == {
        "tenant_id": str(TENANT),
        "integra": True,
        "fila_invalida_id": None,
        "fila_invalida_chain_seq": None,
    }
    assert visto == {"tenant": TENANT, "actor": admin_id}


async def test_verificar_cadena_adulterada_informa_la_posicion(monkeypatch):
    fila = uuid.uuid4()

    async def verificar(pool, tenant_id, *, actor_id=None):
        return pa.ResultadoCadena(tenant_id, False, fila, 12)

    monkeypatch.setattr(pa, "verificar_cadena_audit", verificar)
    app, _, _ = app_de_prueba()
    async with http(app) as c:
        r = await c.get(f"/tenants/{TENANT}/audit-log/verificar-cadena", headers=bearer())
    assert r.status_code == 200
    assert r.json()["integra"] is False
    assert r.json()["fila_invalida_id"] == str(fila) and r.json()["fila_invalida_chain_seq"] == 12


async def test_verificar_cadena_de_plataforma_usa_tenant_none(monkeypatch):
    visto = {}

    async def verificar(pool, tenant_id, *, actor_id=None):
        visto["tenant"] = tenant_id
        return pa.ResultadoCadena(tenant_id=None, integra=True)

    monkeypatch.setattr(pa, "verificar_cadena_audit", verificar)
    app, _, _ = app_de_prueba()
    async with http(app) as c:
        r = await c.get("/plataforma/audit-log/verificar-cadena", headers=bearer())
    assert r.status_code == 200 and r.json()["tenant_id"] is None and r.json()["integra"] is True
    assert visto["tenant"] is None


async def test_verificar_cadena_tenant_inexistente_404_y_id_invalido_422(monkeypatch):
    async def verificar(pool, tenant_id, *, actor_id=None):
        raise pa.TenantNoEncontrado("no")

    monkeypatch.setattr(pa, "verificar_cadena_audit", verificar)
    app, _, _ = app_de_prueba()
    async with http(app) as c:
        assert (await c.get(f"/tenants/{TENANT}/audit-log/verificar-cadena", headers=bearer())).status_code == 404
        assert (await c.get("/tenants/no-es-uuid/audit-log/verificar-cadena", headers=bearer())).status_code == 422


@pytest.mark.parametrize(
    "ruta", [f"/tenants/{TENANT}/audit-log/verificar-cadena", "/plataforma/audit-log/verificar-cadena"]
)
async def test_verificar_cadena_exige_sesion_de_platform_admin_con_aal2(ruta):
    app, _, _ = app_de_prueba()
    async with http(app) as c:
        assert (await c.get(ruta)).status_code == 401
        assert (await c.get(ruta, headers=bearer(is_platform_admin=False))).status_code == 403
        assert (await c.get(ruta, headers=bearer(aal="aal1"))).status_code in (401, 403)


async def test_descartar_limpieza_ok_pasa_datos_y_actor_verificado_y_loguea_al_admin(monkeypatch, caplog):
    visto = {}

    async def descartar(pool, tenant_id, event_id, motivo, *, actor_id=None):
        visto.update(tenant=tenant_id, evento=event_id, motivo=motivo, actor=actor_id)

    monkeypatch.setattr(pa, "descartar_limpieza_calendar", descartar)
    app, _, _ = app_de_prueba()
    admin_id = uuid.uuid4()
    with caplog.at_level(logging.INFO, logger="admin_backend"):
        async with http(app) as c:
            r = await c.post(
                f"/tenants/{TENANT}/limpiezas-calendar/descartar",
                json={"event_id": "evt-1", "motivo": "el evento ya no existe", "actor_id": str(uuid.uuid4())},
                headers=bearer(user_id=admin_id),
            )
    assert r.status_code == 200
    assert r.json() == {"tenant_id": str(TENANT), "event_id": "evt-1", "estado": "descartada"}
    assert visto == {"tenant": TENANT, "evento": "evt-1", "motivo": "el evento ya no existe", "actor": admin_id}
    registro = next(x for x in caplog.records if x.getMessage() == "limpieza_calendar_descartada")
    assert registro.admin_id == str(admin_id) and registro.tenant_id == str(TENANT)
    assert "el evento ya no existe" not in str(registro.__dict__)


async def test_descartar_limpieza_no_pendiente_404(monkeypatch):
    async def descartar(pool, tenant_id, event_id, motivo, *, actor_id=None):
        raise pa.LimpiezaNoPendiente("no")

    monkeypatch.setattr(pa, "descartar_limpieza_calendar", descartar)
    app, _, _ = app_de_prueba()
    async with http(app) as c:
        r = await c.post(
            f"/tenants/{TENANT}/limpiezas-calendar/descartar",
            json={"event_id": "evt-1", "motivo": "m"},
            headers=bearer(),
        )
    assert r.status_code == 404 and r.json()["detail"]["codigo"] == "limpieza_no_pendiente"


@pytest.mark.parametrize("cuerpo", [{"event_id": "evt-1"}, {"event_id": "evt-1", "motivo": ""}, {"motivo": "m"}, {}])
async def test_descartar_limpieza_sin_motivo_o_evento_422(cuerpo):
    app, _, _ = app_de_prueba()
    async with http(app) as c:
        r = await c.post(f"/tenants/{TENANT}/limpiezas-calendar/descartar", json=cuerpo, headers=bearer())
    assert r.status_code == 422


async def test_descartar_limpieza_exige_origin_con_cookie_y_sesion_de_admin():
    ruta = f"/tenants/{TENANT}/limpiezas-calendar/descartar"
    cuerpo = {"event_id": "evt-1", "motivo": "m"}
    app, _, _ = app_de_prueba()
    async with http(app) as c:
        sin_origen = await c.post(ruta, json=cuerpo, headers={"Cookie": "adm_acceso=x"})
        assert sin_origen.status_code == 403 and sin_origen.json()["detail"]["codigo"] == "origen_no_permitido"
        assert (await c.post(ruta, json=cuerpo)).status_code == 403
        assert (await c.post(ruta, json=cuerpo, headers=bearer(is_platform_admin=False))).status_code == 403


async def test_descartar_limpieza_respeta_el_limite_de_body_y_los_headers_de_seguridad():
    app, _, _ = app_de_prueba()
    async with http(app) as c:
        r = await c.post(
            f"/tenants/{TENANT}/limpiezas-calendar/descartar",
            content=b"x" * (1024 * 1024 + 1),
            headers={**bearer(), "Content-Type": "application/json"},
        )
        sano = await c.get("/plataforma/audit-log/verificar-cadena", headers=bearer())
    assert r.status_code == 413
    assert sano.headers.get("x-content-type-options") == "nosniff"
