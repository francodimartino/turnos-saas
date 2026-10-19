"""Fija el mecanismo de cookies de sesión y la defensa CSRF que comparten los backends."""

import ast
from pathlib import Path

import pytest
from fastapi import FastAPI, Request, Response
from httpx import ASGITransport, AsyncClient
from supabase_proxy import TokensSesion

from turnos_sesion import CookiesSesion, MiddlewareSesionCookie

PAQUETE = Path(__file__).resolve().parents[1]
PROHIBIDOS = {"bot_app", "dashboard_backend", "admin_backend"}
ORIGEN = "https://panel.ejemplo.com"
TOKENS = TokensSesion(access_token="acc-123", refresh_token="ref-456", expires_in=3600)


def _app(prefijo: str, **kw) -> tuple[FastAPI, CookiesSesion]:
    cookies = CookiesSesion(
        prefijo, secure=kw.get("secure", True), samesite=kw.get("samesite", "strict"), refresh_max_age=600
    )
    app = FastAPI()

    @app.get("/eco")
    async def eco_get(request: Request):
        return {"authorization": request.headers.get("authorization")}

    @app.post("/eco")
    async def eco_post(request: Request):
        return {"authorization": request.headers.get("authorization")}

    @app.post("/auth/entrar")
    async def entrar(respuesta: Response):
        cookies.poner(respuesta, TOKENS)
        return {}

    @app.post("/auth/salir")
    async def salir(respuesta: Response):
        cookies.borrar(respuesta)
        return {}

    app.add_middleware(MiddlewareSesionCookie, origen_permitido=ORIGEN, cookie_acceso=cookies.acceso)
    return app, cookies


def _cliente(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.mark.parametrize("prefijo", ["dash", "adm"])
async def test_poner_fija_las_dos_cookies_con_sus_atributos(prefijo):
    app, cookies = _app(prefijo)
    assert (cookies.acceso, cookies.refresco) == (f"{prefijo}_acceso", f"{prefijo}_refresco")
    async with _cliente(app) as c:
        r = await c.post("/auth/entrar", headers={"Origin": ORIGEN})
    sc = r.headers.get_list("set-cookie")
    acceso = next(x for x in sc if x.startswith(f"{prefijo}_acceso="))
    refresco = next(x for x in sc if x.startswith(f"{prefijo}_refresco="))
    for x in (acceso, refresco):
        assert "HttpOnly" in x and "Secure" in x and "SameSite=strict" in x
    assert "Path=/;" in acceso and "Max-Age=3600" in acceso
    assert "Path=/auth" in refresco and "Max-Age=600" in refresco
    assert r.headers["cache-control"] == "no-store"


async def test_samesite_y_secure_son_configurables():
    app, _ = _app("dash", secure=False, samesite="lax")
    async with _cliente(app) as c:
        r = await c.post("/auth/entrar", headers={"Origin": ORIGEN})
    for x in r.headers.get_list("set-cookie"):
        assert "SameSite=lax" in x and "Secure" not in x


@pytest.mark.parametrize("prefijo", ["dash", "adm"])
async def test_borrar_expira_ambas_cookies_en_sus_rutas(prefijo):
    app, _ = _app(prefijo)
    async with _cliente(app) as c:
        r = await c.post("/auth/salir", headers={"Origin": ORIGEN})
    sc = r.headers.get_list("set-cookie")
    assert any(x.startswith(f'{prefijo}_acceso=""') and "Max-Age=0" in x and "Path=/;" in x for x in sc)
    assert any(x.startswith(f'{prefijo}_refresco=""') and "Max-Age=0" in x and "Path=/auth" in x for x in sc)
    assert r.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("prefijo", ["dash", "adm"])
async def test_la_cookie_de_acceso_se_convierte_en_bearer_solo_con_su_prefijo(prefijo):
    app, _ = _app(prefijo)
    async with _cliente(app) as c:
        propia = await c.get("/eco", headers={"Cookie": f"{prefijo}_acceso=tok"})
        ajena = await c.get("/eco", headers={"Cookie": "otro_acceso=tok"})
    assert propia.json() == {"authorization": "Bearer tok"}
    assert ajena.json() == {"authorization": None}


async def test_authorization_explicito_no_se_pisa_con_la_cookie():
    app, _ = _app("dash")
    async with _cliente(app) as c:
        r = await c.get("/eco", headers={"Cookie": "dash_acceso=tok", "Authorization": "Bearer otro"})
    assert r.json() == {"authorization": "Bearer otro"}


@pytest.mark.parametrize("prefijo", ["dash", "adm"])
@pytest.mark.parametrize("origen", [None, "https://malo.com", ORIGEN + ".malo.com"])
async def test_metodo_que_escribe_con_origin_ajeno_es_403(prefijo, origen):
    app, _ = _app(prefijo)
    headers = {"Cookie": f"{prefijo}_acceso=tok"}
    if origen:
        headers["Origin"] = origen
    async with _cliente(app) as c:
        r = await c.post("/eco", headers=headers)
    assert r.status_code == 403
    assert r.json() == {"detail": {"codigo": "origen_no_permitido", "mensaje": "Origen no permitido"}}


async def test_metodo_que_escribe_con_origin_propio_pasa():
    app, _ = _app("dash")
    async with _cliente(app) as c:
        r = await c.post("/eco", headers={"Origin": ORIGEN, "Cookie": "dash_acceso=tok"})
    assert r.status_code == 200 and r.json() == {"authorization": "Bearer tok"}


async def test_post_con_authorization_explicito_no_exige_origin():
    app, _ = _app("dash")
    async with _cliente(app) as c:
        r = await c.post("/eco", headers={"Authorization": "Bearer x"})
    assert r.status_code == 200


async def test_metodo_de_lectura_no_chequea_origin():
    app, _ = _app("dash")
    async with _cliente(app) as c:
        r = await c.get("/eco", headers={"Origin": "https://malo.com"})
    assert r.status_code == 200


def test_el_paquete_no_importa_codigo_de_las_apps():
    for archivo in PAQUETE.rglob("*.py"):
        if "tests" in archivo.parts:
            continue
        for nodo in ast.walk(ast.parse(archivo.read_text(encoding="utf-8"))):
            if isinstance(nodo, ast.Import):
                raices = {a.name.split(".")[0] for a in nodo.names}
            elif isinstance(nodo, ast.ImportFrom) and nodo.module:
                raices = {nodo.module.split(".")[0]}
            else:
                continue
            assert not raices & PROHIBIDOS, f"{archivo.name} importa código de una app"
