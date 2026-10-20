"""Forma exacta (claves, tipos y nulos) de las respuestas de panel y visores que ya devuelven dataclasses de dominio."""

import datetime
import uuid

import platform_admin as pa
import pytest
from platform_admin.facturacion import Facturacion
from platform_admin.integraciones import ResultadoIntegracion
from platform_admin.notificaciones import NotificacionConProblema, ProblemasNotificaciones

from admin_backend.tests.soporte import app_de_prueba, bearer, http

T = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
U = uuid.UUID("00000000-0000-0000-0000-0000000000bb")
INSTANTE = datetime.datetime.fromisoformat("2026-01-05T09:30:00+00:00")

TENANT_JSON = {
    "id": str(T), "name": "Peluquería", "slug": "pelu", "plan": "pilot", "status": "active",
    "deactivated_at": None, "paid_until": "2026-12-31", "created_at": "2026-01-05T09:30:00Z",
    "invitacion_pendiente": False, "owner_email": "o@x.test", "owner_nombre": None,
}  # fmt: skip
ENTRADA_JSON = {
    "id": str(U), "tenant_id": str(T), "actor_type": "platform_admin", "actor_id": None,
    "action": "impersonate_view", "entity": "tenants", "entity_id": str(T), "metadata": {"vista": "bookings"},
    "created_at": "2026-01-05T09:30:00Z",
}  # fmt: skip


def _tenant():
    return pa.TenantAdmin(
        id=T, name="Peluquería", slug="pelu", plan="pilot", status="active", deactivated_at=None,
        paid_until=datetime.date(2026, 12, 31), created_at=INSTANTE, invitacion_pendiente=False,
        owner_email="o@x.test", owner_nombre=None,
    )  # fmt: skip


def _entrada():
    return pa.EntradaAudit(
        id=U, tenant_id=T, actor_type="platform_admin", actor_id=None, action="impersonate_view",
        entity="tenants", entity_id=T, metadata={"vista": "bookings"}, created_at=INSTANTE,
    )  # fmt: skip


@pytest.fixture
def escenario(monkeypatch):
    async def listar(pool, *, actor_id=None, status=None):
        return [_tenant()]

    async def obtener(pool, tenant_id, *, actor_id=None):
        return _tenant()

    async def facturacion(pool, tenant_id, *, actor_id=None, **campos):
        return Facturacion(T, datetime.date(2026, 12, 31), None)

    async def audit(pool, tenant_id, **kw):
        return [_entrada()]

    async def problemas(pool, *, actor_id=None, limit=200):
        notificacion = NotificacionConProblema(U, T, U, "telegram", None, "failed", 3, "timeout", INSTANTE, None)
        return ProblemasNotificaciones([notificacion], [_entrada()])

    async def integracion(pool, tenant_id, tipo, config, secreto, *, actor_id=None):
        return ResultadoIntegracion(U, T, tipo, "clave-webhook")

    monkeypatch.setattr(pa, "listar_tenants", listar)
    monkeypatch.setattr(pa, "obtener_tenant", obtener)
    monkeypatch.setattr(pa, "obtener_facturacion", facturacion)
    monkeypatch.setattr(pa, "editar_facturacion", facturacion)
    monkeypatch.setattr(pa, "listar_audit_log", audit)
    monkeypatch.setattr(pa, "visor_problemas_notificaciones", problemas)
    monkeypatch.setattr(pa, "cargar_integracion_tenant", integracion)
    return app_de_prueba()[0]


FACTURACION_JSON = {"tenant_id": str(T), "paid_until": "2026-12-31", "billing_notes": None}


async def test_formas_de_tenants_facturacion_audit_problemas_e_integracion(escenario):
    h = bearer()
    async with http(escenario) as c:
        lista = await c.get("/tenants", headers=h)
        ficha = await c.get(f"/tenants/{T}", headers=h)
        leer = await c.get(f"/tenants/{T}/facturacion", headers=h)
        editar = await c.put(f"/tenants/{T}/facturacion", json={"paid_until": "2026-12-31"}, headers=h)
        audit = await c.get(f"/tenants/{T}/audit-log", headers=h)
        problemas = await c.get("/notificaciones/problemas", headers=h)
        integ = await c.put(f"/tenants/{T}/integraciones/telegram_bot", json={"secret": "x" * 20}, headers=h)
    assert lista.json() == [TENANT_JSON]
    assert ficha.json() == TENANT_JSON
    assert leer.json() == FACTURACION_JSON and editar.json() == FACTURACION_JSON
    assert audit.json() == [ENTRADA_JSON]
    assert problemas.json() == {
        "notificaciones": [
            {
                "id": str(U), "tenant_id": str(T), "end_customer_id": str(U), "channel": "telegram",
                "template": None, "status": "failed", "retry_count": 3, "last_error": "timeout",
                "created_at": "2026-01-05T09:30:00Z", "sent_at": None,
            }
        ],
        "limpiezas_calendar": [ENTRADA_JSON],
    }  # fmt: skip
    assert integ.json() == {
        "integration_id": str(U), "tenant_id": str(T), "integration_type": "telegram_bot",
        "webhook_key": "clave-webhook",
    }  # fmt: skip
