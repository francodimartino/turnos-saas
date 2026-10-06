"""Rutas de tenants, settings y facturación: traducción de errores de dominio y de lo que llega a platform_admin."""

import datetime
import uuid

import httpx
import platform_admin as pa
import pytest

from admin_backend.tests.soporte import BASE, AuthAdminFalso, app_de_prueba, bearer, http

TENANT = uuid.UUID("00000000-0000-0000-0000-0000000000aa")


def _tenant_admin(**cambios):
    base = {
        "id": TENANT,
        "name": "Estética Sol",
        "slug": "sol",
        "plan": "pilot",
        "status": "active",
        "deactivated_at": None,
        "paid_until": None,
        "created_at": datetime.datetime(2026, 1, 1),
        "invitacion_pendiente": False,
    }
    base.update(cambios)
    return pa.TenantAdmin(**base)


async def test_listar_y_obtener_tenant(monkeypatch):
    llamadas = {}

    async def listar(pool, *, actor_id=None, status=None):
        llamadas["listar"] = (actor_id, status)
        return [_tenant_admin()]

    async def obtener(pool, tenant_id, *, actor_id=None):
        return _tenant_admin(id=tenant_id)

    monkeypatch.setattr(pa, "listar_tenants", listar)
    monkeypatch.setattr(pa, "obtener_tenant", obtener)
    app, _, _ = app_de_prueba()
    async with http(app) as c:
        lista = await c.get("/tenants?status=active", headers=bearer())
        uno = await c.get(f"/tenants/{TENANT}", headers=bearer())
    assert lista.status_code == 200 and lista.json()[0]["slug"] == "sol"
    assert llamadas["listar"][1] == "active"
    assert uno.json()["id"] == str(TENANT) and uno.json()["invitacion_pendiente"] is False


async def test_obtener_inexistente_404_y_id_mal_formado_422(monkeypatch):
    async def obtener(pool, tenant_id, *, actor_id=None):
        raise pa.TenantNoEncontrado("no")

    monkeypatch.setattr(pa, "obtener_tenant", obtener)
    app, _, _ = app_de_prueba()
    async with http(app) as c:
        assert (await c.get(f"/tenants/{TENANT}", headers=bearer())).status_code == 404
        assert (await c.get("/tenants/no-es-uuid", headers=bearer())).status_code == 422


async def test_alta_pasa_el_actor_verificado_y_el_cliente_de_auth_admin(monkeypatch):
    capturado = {}

    async def create_tenant(pool, cliente, tenant_data, owner_email, *, actor_id):
        capturado.update(cliente=cliente, data=tenant_data, email=owner_email, actor=actor_id)
        return pa.ResultadoAlta(tenant_id=TENANT, owner_id=uuid.uuid4(), estado=pa.INVITE_PENDIENTE)

    monkeypatch.setattr(pa, "create_tenant", create_tenant)
    auth_admin = AuthAdminFalso()
    app, _, _ = app_de_prueba(auth_admin=auth_admin)
    admin_id = uuid.uuid4()
    async with http(app) as c:
        r = await c.post(
            "/tenants",
            json={"name": "Sol", "slug": "sol", "owner_email": "dueña@ejemplo.test", "status": "purged"},
            headers=bearer(user_id=admin_id),
        )
    assert r.status_code == 201
    assert r.json() == {"tenant_id": str(TENANT), "estado_invitacion": "invite_pendiente"}
    assert capturado["cliente"] is auth_admin
    assert capturado["actor"] == admin_id
    assert capturado["email"] == "dueña@ejemplo.test"
    assert capturado["data"] == {"name": "Sol", "slug": "sol"}  # el status del body se ignora


async def test_alta_slug_repetido_409_y_datos_invalidos_422(monkeypatch):
    async def create_tenant(pool, cliente, tenant_data, owner_email, *, actor_id):
        if tenant_data["slug"] == "repetido":
            raise pa.SlugEnUso("dup")
        raise pa.DatosTenantInvalidos("mal")

    monkeypatch.setattr(pa, "create_tenant", create_tenant)
    app, _, _ = app_de_prueba()
    cuerpo = {"name": "N", "owner_email": "a@b.co"}
    async with http(app) as c:
        dup = await c.post("/tenants", json={**cuerpo, "slug": "repetido"}, headers=bearer())
        malo = await c.post("/tenants", json={**cuerpo, "slug": "OTRO"}, headers=bearer())
        sin_email = await c.post("/tenants", json={"name": "N", "slug": "x"}, headers=bearer())
    assert dup.status_code == 409
    assert malo.status_code == 422
    assert sin_email.status_code == 422


@pytest.mark.parametrize(
    ("ruta", "funcion"),
    [
        ("suspender", "suspender"),
        ("levantar-suspension", "levantar_suspension"),
        ("dar-de-baja", "dar_de_baja"),
        ("reactivar", "reactivar"),
    ],
)
async def test_ciclo_de_vida_204_y_409_ante_transicion_invalida(monkeypatch, ruta, funcion):
    modo = {"falla": False}
    actores = []

    async def accion(pool, tenant_id, *, actor_id):
        actores.append((tenant_id, actor_id))
        if modo["falla"]:
            raise pa.TransicionInvalida(funcion, "purged")

    monkeypatch.setattr(pa, funcion, accion)
    app, _, _ = app_de_prueba()
    admin_id = uuid.uuid4()
    async with http(app) as c:
        ok = await c.post(f"/tenants/{TENANT}/{ruta}", headers=bearer(user_id=admin_id))
        modo["falla"] = True
        invalida = await c.post(f"/tenants/{TENANT}/{ruta}", headers=bearer(user_id=admin_id))
    assert ok.status_code == 204
    assert actores[0] == (TENANT, admin_id)
    assert invalida.status_code == 409
    assert invalida.json()["detail"]["codigo"] == "transicion_invalida"


async def test_ciclo_de_vida_tenant_inexistente_404(monkeypatch):
    async def accion(pool, tenant_id, *, actor_id):
        raise pa.TenantNoEncontrado("no")

    monkeypatch.setattr(pa, "suspender", accion)
    app, _, _ = app_de_prueba()
    async with http(app) as c:
        assert (await c.post(f"/tenants/{TENANT}/suspender", headers=bearer())).status_code == 404


async def test_reenviar_invitacion_usa_el_email_guardado_y_409_si_no_hay_pendiente(monkeypatch):
    capturado = {}

    async def reenviar(pool, cliente, tenant_id, owner_email=None, *, actor_id):
        capturado.setdefault("emails", []).append(owner_email)
        if tenant_id != TENANT:
            raise pa.InvitacionNoPendiente("ya tiene cuenta")
        return pa.ResultadoInvitacion(tenant_id=tenant_id, estado=pa.INVITE_OK)

    monkeypatch.setattr(pa, "reenviar_invitacion", reenviar)
    app, _, _ = app_de_prueba()
    async with http(app) as c:
        ok = await c.post(f"/tenants/{TENANT}/reenviar-invitacion", headers=bearer())
        corregido = await c.post(
            f"/tenants/{TENANT}/reenviar-invitacion", json={"owner_email": "nuevo@ejemplo.test"}, headers=bearer()
        )
        otro = await c.post(f"/tenants/{uuid.uuid4()}/reenviar-invitacion", headers=bearer())
    assert ok.status_code == 200 and ok.json() == {"tenant_id": str(TENANT), "estado_invitacion": "ok"}
    assert corregido.status_code == 200 and capturado["emails"][:2] == [None, "nuevo@ejemplo.test"]
    assert otro.status_code == 409


async def test_settings_get_put_y_validacion(monkeypatch):
    async def listar(pool, *, actor_id=None):
        return {"tenant_deletion_grace_period_days": 30}

    async def editar(pool, clave, valor, *, actor_id):
        if clave != "tenant_deletion_grace_period_days":
            raise pa.SettingInvalido("clave desconocida")
        if valor == -1:
            raise pa.SettingInvalido("valor")
        return {"key": clave, "desde": 30, "hasta": valor}

    monkeypatch.setattr(pa, "listar_settings", listar)
    monkeypatch.setattr(pa, "editar_setting", editar)
    app, _, _ = app_de_prueba()
    async with http(app) as c:
        lista = await c.get("/settings", headers=bearer())
        ok = await c.put("/settings/tenant_deletion_grace_period_days", json={"valor": 45}, headers=bearer())
        malo = await c.put("/settings/tenant_deletion_grace_period_days", json={"valor": -1}, headers=bearer())
        clave = await c.put("/settings/otra", json={"valor": 1}, headers=bearer())
        sin_valor = await c.put("/settings/tenant_deletion_grace_period_days", json={}, headers=bearer())
    assert lista.status_code == 200 and 30 in lista.json().values()
    assert ok.json() == {"key": "tenant_deletion_grace_period_days", "desde": 30, "hasta": 45}
    assert malo.status_code == 422 and clave.status_code == 422 and sin_valor.status_code == 422


async def test_facturacion_get_put_parcial_y_null_borra(monkeypatch):
    recibido = {}

    async def obtener(pool, tenant_id, *, actor_id=None):
        return pa.Facturacion(tenant_id, datetime.date(2026, 11, 1), "nota")

    async def editar(pool, tenant_id, *, actor_id, **campos):
        recibido.update(campos)
        return pa.Facturacion(tenant_id, campos.get("paid_until", None), "nota")

    monkeypatch.setattr(pa, "obtener_facturacion", obtener)
    monkeypatch.setattr(pa, "editar_facturacion", editar)
    app, _, _ = app_de_prueba()
    async with http(app) as c:
        leer = await c.get(f"/tenants/{TENANT}/facturacion", headers=bearer())
        solo_fecha = await c.put(f"/tenants/{TENANT}/facturacion", json={"paid_until": "2026-12-01"}, headers=bearer())
        assert set(recibido) == {"paid_until"}  # billing_notes no se tocó
        recibido.clear()
        borrar = await c.put(f"/tenants/{TENANT}/facturacion", json={"billing_notes": None}, headers=bearer())
        assert recibido == {"billing_notes": None}
        vacio = await c.put(f"/tenants/{TENANT}/facturacion", json={}, headers=bearer())
    assert leer.json() == {"tenant_id": str(TENANT), "paid_until": "2026-11-01", "billing_notes": "nota"}
    assert solo_fecha.status_code == 200 and borrar.status_code == 200
    assert vacio.status_code == 422


async def test_error_inesperado_da_500_sin_detalle(monkeypatch):
    async def roto(pool, *, actor_id=None, status=None):
        raise RuntimeError("detalle interno: password=hunter2")

    monkeypatch.setattr(pa, "listar_tenants", roto)
    app, _, _ = app_de_prueba()
    transporte = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transporte, base_url=BASE) as c:
        r = await c.get("/tenants", headers=bearer())
    assert r.status_code == 500
    assert "hunter2" not in r.text
