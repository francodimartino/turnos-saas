"""Refresh con su propio límite por IP y JWKS caído en logout/audit (piezas simuladas)."""

import uuid

from platform_auth import ClavesNoDisponibles
from turnos_proteccion_login import LimitesLogin

from dashboard_backend.app import crear_app
from dashboard_backend.tests.soporte import (
    ORIGEN,
    PoolFalso,
    SupabaseSimulado,
    app_de_prueba,
    config_prueba,
    http,
    sesion_json,
    token,
)

H = {"Origin": ORIGEN}
CRED = {"email": "a@b.co", "password": "x"}
TENANT = uuid.uuid4()


def _config(por_ip=100, por_email=100, **extra):
    return config_prueba(login_limites=LimitesLogin(por_ip=por_ip, por_email=por_email, ventana_seg=900), **extra)


def _ok(sb):
    sb.responder("POST", "/auth/v1/token", 200, sesion_json(access=token(tenant_id=TENANT)))


class VerificadorCaido:
    """JWKS no disponible: ningún token se puede verificar, y no es culpa del token."""

    def verificar(self, _token):
        raise ClavesNoDisponibles()


async def test_agotar_el_limite_de_login_no_impide_refrescar_desde_la_misma_ip():
    app, sb, _ = app_de_prueba(config=_config(por_ip=1))
    _ok(sb)
    async with http(app) as c:
        assert (await c.post("/auth/login", json=CRED, headers=H)).status_code == 200
        assert (await c.post("/auth/login", json=CRED, headers=H)).status_code == 429
        r = await c.post("/auth/refrescar", headers={**H, "Cookie": "dash_refresco=abc"})
    assert r.status_code == 200


async def test_refrescar_tiene_su_propio_tope_configurable_con_429_y_retry_after():
    app, sb, _ = app_de_prueba(config=_config(refresco_limite_ip=2))
    _ok(sb)
    async with http(app) as c:
        h = {**H, "Cookie": "dash_refresco=abc"}
        assert (await c.post("/auth/refrescar", headers=h)).status_code == 200
        assert (await c.post("/auth/refrescar", headers=h)).status_code == 200
        r = await c.post("/auth/refrescar", headers=h)
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1


def _app_jwks_caido():
    sb = SupabaseSimulado()
    pool = PoolFalso()
    app = crear_app(
        config_prueba(),
        verificador=VerificadorCaido(),
        pool=pool,
        cliente_auth=sb.cliente(),
        configurar_observabilidad=False,
    )
    return app, sb, pool


async def test_logout_con_jwks_caido_cierra_la_sesion_omite_solo_el_audit_y_loguea_el_motivo(caplog):
    app, sb, pool = _app_jwks_caido()
    sb.responder("POST", "/auth/v1/logout", 204)
    async with http(app) as c:
        r = await c.post("/auth/logout", headers={**H, "Cookie": f"dash_acceso={token(tenant_id=TENANT)}"})
    assert r.status_code == 204
    assert [x.url.path for x in sb.llamadas] == ["/auth/v1/logout"]
    assert pool.conn.auditoria == []
    assert any(x.getMessage() == "audit_omitido_jwks_no_disponible" for x in caplog.records)


async def test_refrescar_con_jwks_caido_omite_el_audit_y_loguea_el_motivo(caplog):
    app, sb, pool = _app_jwks_caido()
    _ok(sb)
    async with http(app) as c:
        r = await c.post("/auth/refrescar", headers={**H, "Cookie": "dash_refresco=abc"})
    assert r.status_code == 200
    assert pool.conn.auditoria == []
    assert any(x.getMessage() == "audit_omitido_jwks_no_disponible" for x in caplog.records)
