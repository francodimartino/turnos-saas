"""Visores (audit_log, notificaciones con problemas) y carga de integraciones: HTTP <-> dominio, sin base real."""

import datetime
import uuid

import platform_admin as pa
import pytest

from admin_backend.tests.soporte import app_de_prueba, bearer, http

TENANT = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
AHORA = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=datetime.timezone.utc)  # noqa: UP017


def _entrada(**c):
    base = {
        "id": uuid.uuid4(),
        "tenant_id": TENANT,
        "actor_type": "staff",
        "actor_id": None,
        "action": "x",
        "entity": None,
        "entity_id": None,
        "metadata": {"k": 1},
        "created_at": AHORA,
    }
    base.update(c)
    return pa.EntradaAudit(**base)


async def test_audit_log_pasa_filtros_y_actor_verificado(monkeypatch):
    capturado = {}

    async def listar(pool, tenant_id, **kw):
        capturado.update(tenant=tenant_id, **kw)
        return [_entrada(action="cancelar")]

    monkeypatch.setattr(pa, "listar_audit_log", listar)
    app, _, _ = app_de_prueba()
    admin_id = uuid.uuid4()
    async with http(app) as c:
        r = await c.get(
            f"/tenants/{TENANT}/audit-log?action=cancelar&actor_type=staff&limit=10&offset=5",
            headers=bearer(user_id=admin_id),
        )
    assert r.status_code == 200
    assert r.json()[0]["action"] == "cancelar"
    assert capturado["tenant"] == TENANT and capturado["actor_id"] == admin_id
    assert capturado["action"] == "cancelar" and capturado["actor_type"] == "staff"
    assert capturado["limit"] == 10 and capturado["offset"] == 5


async def test_audit_log_tenant_inexistente_404_y_actor_type_invalido_422(monkeypatch):
    async def listar(pool, tenant_id, **kw):
        if kw["actor_type"] == "raro":
            raise pa.DatosInvalidos("actor_type inválido")
        raise pa.TenantNoEncontrado("no")

    monkeypatch.setattr(pa, "listar_audit_log", listar)
    app, _, _ = app_de_prueba()
    async with http(app) as c:
        assert (await c.get(f"/tenants/{TENANT}/audit-log", headers=bearer())).status_code == 404
        assert (await c.get(f"/tenants/{TENANT}/audit-log?actor_type=raro", headers=bearer())).status_code == 422


async def test_problemas_de_notificaciones_incluye_limpiezas_de_calendar(monkeypatch):
    visto = {}

    async def visor(pool, *, actor_id=None, ahora=None, limit=200):
        visto.update(actor=actor_id, limit=limit)
        notif = pa.NotificacionConProblema(
            id=uuid.uuid4(),
            tenant_id=TENANT,
            end_customer_id=uuid.uuid4(),
            channel="telegram",
            template="recordatorio",
            status="failed",
            retry_count=3,
            last_error="timeout",
            created_at=AHORA,
            sent_at=None,
        )
        return pa.ProblemasNotificaciones(
            notificaciones=[notif], limpiezas_calendar=[_entrada(action="calendar_scrub_failed")]
        )

    monkeypatch.setattr(pa, "visor_problemas_notificaciones", visor)
    app, _, _ = app_de_prueba()
    admin_id = uuid.uuid4()
    async with http(app) as c:
        r = await c.get("/notificaciones/problemas?limit=50", headers=bearer(user_id=admin_id))
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["notificaciones"][0]["status"] == "failed"
    assert cuerpo["limpiezas_calendar"][0]["action"] == "calendar_scrub_failed"
    assert visto == {"actor": admin_id, "limit": 50}


async def test_cargar_integracion_nunca_devuelve_ni_loguea_el_secret(monkeypatch, caplog):
    capturado = {}
    secreto = "123456:ABC-token-secreto-del-bot"

    async def cargar(pool, tenant_id, tipo, config, secret, *, actor_id):
        capturado.update(tenant=tenant_id, tipo=tipo, config=config, secret=secret, actor=actor_id)
        return pa.ResultadoIntegracion(
            integration_id=uuid.uuid4(), tenant_id=tenant_id, integration_type=tipo, webhook_key="wk-1"
        )

    monkeypatch.setattr(pa, "cargar_integracion_tenant", cargar)
    app, _, _ = app_de_prueba()
    admin_id = uuid.uuid4()
    caplog.set_level("DEBUG")
    async with http(app) as c:
        r = await c.put(
            f"/tenants/{TENANT}/integraciones/telegram_bot",
            json={"config": {"bot_username": "mi_bot"}, "secret": secreto},
            headers=bearer(user_id=admin_id),
        )
    assert r.status_code == 200
    assert r.json()["webhook_key"] == "wk-1" and r.json()["integration_type"] == "telegram_bot"
    assert secreto not in r.text
    assert secreto not in caplog.text
    assert capturado["tipo"] == "telegram_bot" and capturado["actor"] == admin_id
    assert capturado["config"] == {"bot_username": "mi_bot"}
    revelar = getattr(capturado["secret"], "revelar", None)
    assert (revelar() if revelar else capturado["secret"]) == secreto


async def test_cargar_integracion_errores_no_repiten_el_secret():
    secreto = "otro-secreto-que-no-debe-aparecer"
    app, _, _ = app_de_prueba()
    async with http(app) as c:
        # tipo no soportado -> 422 (dominio real, sin monkeypatch)
        r = await c.put(
            f"/tenants/{TENANT}/integraciones/whatsapp_business",
            json={"config": {}, "secret": secreto},
            headers=bearer(),
        )
        assert r.status_code == 422 and secreto not in r.text
        # body mal formado: el 422 de validación no devuelve el valor recibido
        r = await c.put(
            f"/tenants/{TENANT}/integraciones/telegram_bot",
            json={"config": "no-es-objeto", "secret": secreto},
            headers=bearer(),
        )
        assert r.status_code == 422 and secreto not in r.text
        r = await c.put(
            f"/tenants/{TENANT}/integraciones/telegram_bot",
            json={"config": {}, "secret": {"anidado": secreto}},
            headers=bearer(),
        )
        assert r.status_code == 422 and secreto not in r.text


async def test_cargar_integracion_tenant_inexistente_404(monkeypatch):
    async def cargar(*a, **k):
        raise pa.TenantNoEncontrado("no")

    monkeypatch.setattr(pa, "cargar_integracion_tenant", cargar)
    app, _, _ = app_de_prueba()
    async with http(app) as c:
        r = await c.put(
            f"/tenants/{TENANT}/integraciones/telegram_bot", json={"config": {}, "secret": "s"}, headers=bearer()
        )
    assert r.status_code == 404


@pytest.mark.parametrize(
    ("metodo", "ruta"),
    [
        ("GET", f"/tenants/{TENANT}/audit-log"),
        ("GET", "/notificaciones/problemas"),
        ("PUT", f"/tenants/{TENANT}/integraciones/telegram_bot"),
    ],
)
async def test_sin_sesion_de_admin_401_o_403(metodo, ruta):
    app, _, _ = app_de_prueba()
    async with http(app) as c:
        sin = await c.request(metodo, ruta, json={"config": {}, "secret": "s"})
        no_admin = await c.request(
            metodo, ruta, json={"config": {}, "secret": "s"}, headers=bearer(is_platform_admin=False)
        )
    assert sin.status_code in (401, 403)
    assert no_admin.status_code == 403
