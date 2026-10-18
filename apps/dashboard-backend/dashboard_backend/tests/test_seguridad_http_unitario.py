"""Headers de seguridad y tope de body del backend, sin pisar CORS ni los headers de las respuestas de error."""

from dashboard_backend.tests.soporte import ORIGEN, app_de_prueba, http

H = {"Origin": ORIGEN}


def _seguros(r):
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert "max-age=" in r.headers["strict-transport-security"]
    assert r.headers["cache-control"] == "no-store"


async def test_health_y_404_llevan_headers_de_seguridad():
    app, *_ = app_de_prueba()
    async with http(app) as c:
        _seguros(await c.get("/health"))
        r = await c.get("/ruta-inexistente")
    assert r.status_code == 404
    _seguros(r)


async def test_422_conserva_su_cuerpo_y_lleva_headers():
    app, *_ = app_de_prueba()
    async with http(app) as c:
        r422 = await c.post("/auth/login", json={"email": 1, "password": "secreto-raro"}, headers=H)
    assert r422.status_code == 422
    assert "secreto" not in r422.text
    _seguros(r422)


async def test_cors_sigue_funcionando_con_los_headers_nuevos():
    app, *_ = app_de_prueba()
    async with http(app) as c:
        r = await c.options(
            "/auth/login",
            headers={**H, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "Content-Type"},
        )
        r2 = await c.get("/health", headers=H)
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == ORIGEN
    assert r.headers["access-control-allow-credentials"] == "true"
    assert r2.headers["access-control-allow-origin"] == ORIGEN
    _seguros(r2)


async def test_post_login_con_cuerpo_enorme_responde_413_con_content_length():
    app, *_ = app_de_prueba()
    async with http(app) as c:
        r = await c.post(
            "/auth/login", content=b"x" * (1024 * 1024 + 1), headers={**H, "Content-Type": "application/json"}
        )
    assert r.status_code == 413
    assert r.json()["detail"]["codigo"] == "cuerpo_demasiado_grande"
    _seguros(r)
    assert r.headers["access-control-allow-origin"] == ORIGEN


async def test_post_login_en_streaming_sin_content_length_responde_413():
    app, *_ = app_de_prueba()

    async def trozos():
        for _ in range(40):
            yield b"x" * 65536

    async with http(app) as c:
        r = await c.post("/auth/login", content=trozos(), headers={**H, "Content-Type": "application/json"})
    assert r.status_code == 413


async def test_login_normal_no_se_ve_afectado_por_el_limite():
    app, *_ = app_de_prueba()
    async with http(app) as c:
        r = await c.post("/auth/login", json={"email": "a@b.co", "password": "x" * 200}, headers=H)
    assert r.status_code != 413
    _seguros(r)


async def test_500_no_manejado_lleva_headers_y_cuerpo_generico():
    import httpx

    app, *_ = app_de_prueba()

    @app.get("/ruta-que-explota")
    async def explota():
        raise RuntimeError("detalle-interno-secreto")

    transporte = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transporte, base_url="http://t") as c:
        r = await c.get("/ruta-que-explota", headers=H)
    assert r.status_code == 500
    _seguros(r)
    assert "server" not in r.headers
    assert "detalle-interno-secreto" not in r.text
    assert "Traceback" not in r.text
