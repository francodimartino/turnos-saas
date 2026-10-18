"""Límite por email solo de fallos, límite propio del refresh y JWKS caído en logout/audit (piezas simuladas)."""

import uuid

from platform_auth import ClavesNoDisponibles

from admin_backend.app import crear_app
from admin_backend.tests.soporte import (
    ORIGEN,
    AuthAdminFalso,
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


def _ok(sb):
    sb.responder("POST", "/auth/v1/token", 200, sesion_json(access=token(aal="aal1")))


def _mal(sb):
    sb.responder("POST", "/auth/v1/token", 400, {"error_code": "invalid_credentials"})


class VerificadorCaido:
    """JWKS no disponible: ningún token se puede verificar, y no es culpa del token."""

    def verificar(self, _token):
        raise ClavesNoDisponibles()


async def test_n_menos_1_fallos_y_un_acierto_no_bloquean_y_los_aciertos_no_cuentan():
    app, sb, _ = app_de_prueba(config=config_prueba(login_limite_ip=100, login_limite_email=3))
    async with http(app) as c:
        _mal(sb)
        for _ in range(2):
            assert (await c.post("/auth/login", json=CRED, headers=H)).status_code == 401
        _ok(sb)
        for _ in range(5):  # los aciertos no consumen el cupo del email
            assert (await c.post("/auth/login", json=CRED, headers=H)).status_code == 200


async def test_n_fallos_bloquean_con_429_antes_de_validar_las_credenciales():
    app, sb, _ = app_de_prueba(config=config_prueba(login_limite_ip=100, login_limite_email=2))
    async with http(app) as c:
        _mal(sb)
        for _ in range(2):
            assert (await c.post("/auth/login", json=CRED, headers=H)).status_code == 401
        _ok(sb)  # la clave ahora sería correcta, pero el email ya está bloqueado
        llamadas = len(sb.llamadas)
        r = await c.post("/auth/login", json=CRED, headers=H)
        otro = await c.post("/auth/login", json={**CRED, "email": "otro@b.co"}, headers=H)
    assert r.status_code == 429 and r.json()["detail"]["codigo"] == "demasiados_intentos"
    assert int(r.headers["retry-after"]) >= 1
    assert len(sb.llamadas) == llamadas + 1  # el bloqueado no llegó a Supabase; solo el de otro email
    assert otro.status_code == 200


async def test_agotar_el_limite_de_login_no_impide_refrescar_desde_la_misma_ip():
    app, sb, _ = app_de_prueba(config=config_prueba(login_limite_ip=1, login_limite_email=100))
    _ok(sb)
    async with http(app) as c:
        assert (await c.post("/auth/login", json=CRED, headers=H)).status_code == 200
        assert (await c.post("/auth/login", json=CRED, headers=H)).status_code == 429
        r = await c.post("/auth/refrescar", headers={**H, "Cookie": "adm_refresco=abc"})
    assert r.status_code == 200


async def test_refrescar_tiene_su_propio_tope_configurable_con_429_y_retry_after():
    app, sb, _ = app_de_prueba(config=config_prueba(login_limite_ip=100, refresco_limite_ip=2))
    _ok(sb)
    async with http(app) as c:
        h = {**H, "Cookie": "adm_refresco=abc"}
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
        pool_readonly=PoolFalso(),
        cliente_auth=sb.cliente(),
        cliente_auth_admin=AuthAdminFalso(),
        configurar_observabilidad=False,
    )
    return app, sb, pool


async def test_logout_con_jwks_caido_cierra_la_sesion_omite_solo_el_audit_y_loguea_el_motivo(caplog):
    app, sb, pool = _app_jwks_caido()
    sb.responder("POST", "/auth/v1/logout", 204)
    async with http(app) as c:
        r = await c.post("/auth/logout", headers={**H, "Cookie": f"adm_acceso={token(user_id=uuid.uuid4())}"})
    assert r.status_code == 204
    assert [x.url.path for x in sb.llamadas] == ["/auth/v1/logout"]
    assert pool.conn.auditados == []
    assert any(x.getMessage() == "audit_omitido_jwks_no_disponible" for x in caplog.records)


async def test_refrescar_con_jwks_caido_omite_el_audit_y_loguea_el_motivo(caplog):
    app, sb, pool = _app_jwks_caido()
    _ok(sb)
    async with http(app) as c:
        r = await c.post("/auth/refrescar", headers={**H, "Cookie": "adm_refresco=abc"})
    assert r.status_code == 200
    assert pool.conn.auditados == []
    assert any(x.getMessage() == "audit_omitido_jwks_no_disponible" for x in caplog.records)
