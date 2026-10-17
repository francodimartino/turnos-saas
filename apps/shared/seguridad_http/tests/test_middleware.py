"""Headers de seguridad y tope de body, sobre una app ASGI mínima (sin servicios externos)."""

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from seguridad_http import (
    LIMITE_BODY_POR_DEFECTO,
    MiddlewareHeadersSeguridad,
    MiddlewareLimiteBody,
    limite_body_desde_entorno,
)


def _app(limite=100):
    app = FastAPI()

    @app.get("/x")
    async def x():
        return {"ok": True}

    @app.get("/propio")
    async def propio():
        return JSONResponse({"ok": True}, headers={"Cache-Control": "private, max-age=5"})

    @app.post("/eco")
    async def eco(request: Request):
        return {"largo": len(await request.body())}

    @app.post("/json")
    async def con_json(datos: dict):
        return {"claves": len(datos)}

    app.add_middleware(MiddlewareLimiteBody, limite_bytes=limite)
    app.add_middleware(MiddlewareHeadersSeguridad)
    return app


def _cliente(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def test_headers_en_respuesta_ok_y_404():
    async with _cliente(_app()) as c:
        for r in (await c.get("/x"), await c.get("/no-existe")):
            assert int(r.headers["strict-transport-security"].split("max-age=")[1].split(";")[0]) >= 31536000
            assert r.headers["x-content-type-options"] == "nosniff"
            assert r.headers["x-frame-options"] == "DENY"
            assert r.headers["referrer-policy"] == "no-referrer"
            assert r.headers["cache-control"] == "no-store"


async def test_no_pisa_cache_control_de_la_ruta():
    async with _cliente(_app()) as c:
        r = await c.get("/propio")
    assert r.headers["cache-control"] == "private, max-age=5"
    assert r.headers["x-frame-options"] == "DENY"


async def test_content_length_mayor_al_limite_responde_413_sin_leer_el_cuerpo():
    leidos = []

    async def app_que_lee(scope, receive, send):
        leidos.append(await receive())

    mw = MiddlewareLimiteBody(app_que_lee, limite_bytes=10)
    enviados = []

    async def receive():
        raise AssertionError("no debe leerse el cuerpo")

    async def send(m):
        enviados.append(m)

    scope = {"type": "http", "method": "POST", "path": "/", "headers": [(b"content-length", b"11")]}
    await mw(scope, receive, send)
    assert enviados[0]["status"] == 413
    assert leidos == []


async def test_streaming_sin_content_length_responde_413_y_corta_la_lectura():
    pedidos = []

    async def receive():
        pedidos.append(1)
        return {"type": "http.request", "body": b"x" * 60, "more_body": True}

    enviados = []

    async def send(m):
        enviados.append(m)

    app = _app(limite=100)
    scope = {
        "type": "http", "method": "POST", "path": "/eco", "headers": [], "query_string": b"",
        "http_version": "1.1", "scheme": "http", "server": ("t", 80), "client": ("c", 1), "root_path": "",
    }  # fmt: skip
    await app(scope, receive, send)
    assert enviados[0]["status"] == 413
    assert enviados[0]["headers"] and any(h[0] == b"x-frame-options" for h in enviados[0]["headers"])
    assert len(pedidos) <= 3  # no consumió un stream infinito


async def test_streaming_httpx_sin_content_length_413_aunque_la_ruta_parsea_json():
    async def trozos():
        for _ in range(50):
            yield b'{"a": "' + b"y" * 50 + b'"}'

    async with _cliente(_app(limite=100)) as c:
        r = await c.post("/json", content=trozos(), headers={"Content-Type": "application/json"})
    assert r.status_code == 413
    assert r.json()["detail"]["codigo"] == "cuerpo_demasiado_grande"


async def test_cuerpo_bajo_el_limite_pasa():
    async with _cliente(_app(limite=100)) as c:
        r = await c.post("/eco", content=b"x" * 100)
    assert r.status_code == 200 and r.json() == {"largo": 100}


async def test_413_lleva_headers_de_seguridad_y_content_length_declarado():
    async with _cliente(_app(limite=100)) as c:
        r = await c.post("/eco", content=b"x" * 101)
    assert r.status_code == 413
    assert r.headers["x-content-type-options"] == "nosniff"


def test_limite_desde_entorno(monkeypatch):
    monkeypatch.delenv("HTTP_MAX_BODY_BYTES", raising=False)
    assert limite_body_desde_entorno() == LIMITE_BODY_POR_DEFECTO
    monkeypatch.setenv("HTTP_MAX_BODY_BYTES", "2048")
    assert limite_body_desde_entorno() == 2048
    for malo in ("abc", "0", "-5", ""):
        monkeypatch.setenv("HTTP_MAX_BODY_BYTES", malo)
        assert limite_body_desde_entorno() == LIMITE_BODY_POR_DEFECTO


@pytest.mark.parametrize("tipo", ["lifespan", "websocket"])
async def test_scopes_no_http_pasan_de_largo(tipo):
    vistos = []

    async def app(scope, receive, send):
        vistos.append(scope["type"])

    for mw in (MiddlewareLimiteBody(app, limite_bytes=1), MiddlewareHeadersSeguridad(app)):
        await mw({"type": tipo}, None, None)
    assert vistos == [tipo, tipo]
