"""Fija el flujo `/auth/*` compartido: lo que cada backend parametriza y lo que nunca cambia."""

import uuid

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from platform_auth import ClavesNoDisponibles, IdentidadJWT
from proteccion_login import ConfigIp, LimitadorIntentos, VerificadorTurnstile
from supabase_proxy import CredencialesInvalidas, ErrorSupabaseAuth, TokensSesion
from turnos_http import respuesta_error

from turnos_sesion import CookiesSesion, DependenciasSesion, ServiciosAuth, rutas_auth

USUARIO = uuid.uuid4()
TOKENS = TokensSesion(access_token="acc-123", refresh_token="ref-456", expires_in=3600)


class _Cliente:
    def __init__(self):
        self.cerradas: list[str] = []
        self.falla = False

    async def login_password(self, email, password):
        if self.falla:
            raise CredencialesInvalidas()
        return TOKENS

    async def cerrar_sesion(self, acceso):
        self.cerradas.append(acceso)


class _Verificador:
    jwks_caido = False

    def verificar(self, token):
        if self.jwks_caido:
            raise ClavesNoDisponibles()
        return IdentidadJWT(user_id=USUARIO, tenant_id=None, is_owner=False, is_platform_admin=True, aal="aal2")


def _app(limite_ip=100, limite_email=100, exige_mfa=True):
    cliente, verificador, auditoria = _Cliente(), _Verificador(), []

    async def auditar(accion, quien, metadata=None):
        auditoria.append((accion, quien.user_id if quien else None, metadata))

    async def cuerpo_sesion(request):
        return {"user_id": "u-1", "aal": "aal1"}

    app = FastAPI()

    @app.exception_handler(ErrorSupabaseAuth)
    async def _error_supabase(_request, error):
        return respuesta_error(error.status_http, error.codigo, error.mensaje)

    app.include_router(rutas_auth.router)
    app.state.dependencias = DependenciasSesion(lambda r: None, lambda r: None, actor_type="x")
    app.state.auth = ServiciosAuth(
        cliente=cliente,
        verificador=verificador,
        cookies=CookiesSesion("t", secure=True, samesite="strict", refresh_max_age=60),
        turnstile=VerificadorTurnstile(None),
        config_ip=ConfigIp(),
        por_ip=LimitadorIntentos(limite_ip, 60),
        por_email=LimitadorIntentos(limite_email, 60),
        por_ip_refresco=LimitadorIntentos(100, 60),
        nombre_totp="Turnos",
        codigo_sin_acceso="sin_acceso_x",
        mensaje_sin_acceso="sin acceso",
        tiene_acceso=lambda i: i.is_platform_admin,
        exige_mfa=lambda i: exige_mfa,
        auditar=auditar,
        cuerpo_sesion=cuerpo_sesion,
    )
    return app, cliente, verificador, auditoria


def _http(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def test_login_pone_cookies_fuera_del_body_y_audita():
    app, _, _, auditoria = _app()
    async with _http(app) as c:
        r = await c.post("/auth/login", json={"email": "a@b.com", "password": "x"})
    assert r.status_code == 200
    assert r.json() == {"siguiente_paso": "enrolar_mfa", "expira_en": 3600}
    assert "acc-123" not in r.text and "t_acceso" in r.headers["set-cookie"]
    assert auditoria == [("auth_login_succeeded", USUARIO, {"siguiente_paso": "enrolar_mfa"})]


async def test_si_el_backend_no_exige_mfa_sin_factores_el_paso_es_listo():
    app, *_ = _app(exige_mfa=False)
    async with _http(app) as c:
        r = await c.post("/auth/login", json={"email": "a@b.com", "password": "x"})
    assert r.json()["siguiente_paso"] == "listo"


async def test_el_limite_por_email_cuenta_solo_fallos_y_responde_429_con_retry_after():
    app, cliente, *_ = _app(limite_email=1)
    cliente.falla = True
    async with _http(app) as c:
        primero = await c.post("/auth/login", json={"email": "a@b.com", "password": "x"})
        segundo = await c.post("/auth/login", json={"email": "a@b.com", "password": "x"})
    assert primero.status_code != 429
    assert segundo.status_code == 429 and int(segundo.headers["retry-after"]) >= 1
    assert segundo.json()["detail"]["codigo"] == "demasiados_intentos"


async def test_logout_cierra_la_sesion_aunque_el_jwks_no_este_disponible_y_omite_el_audit():
    app, cliente, verificador, auditoria = _app()
    verificador.jwks_caido = True
    async with _http(app) as c:
        r = await c.post("/auth/logout", headers={"authorization": "Bearer abc"})
    assert r.status_code == 204 and cliente.cerradas == ["abc"] and auditoria == []


async def test_sesion_delega_en_el_cuerpo_del_backend():
    app, *_ = _app()
    async with _http(app) as c:
        r = await c.get("/auth/sesion")
    assert r.json() == {"user_id": "u-1", "aal": "aal1"}


def test_no_importa_codigo_de_las_apps():
    import ast
    from pathlib import Path

    fuente = Path(rutas_auth.__file__).read_text(encoding="utf-8")
    nombres = {
        n.module.split(".")[0] for n in ast.walk(ast.parse(fuente)) if isinstance(n, ast.ImportFrom) and n.module
    }
    assert not nombres & {"bot_app", "dashboard_backend", "admin_backend"}
