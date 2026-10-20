"""Headers de seguridad y tope de body en el bot; el webhook de Telegram sigue aceptando updates normales."""

import httpx

from turnos_bot import crear_app
from turnos_bot.tests.conftest import update
from turnos_bot.tests.test_webhook_unitario import CABECERA, RUTA, enviar, montar


def _seguros(r):
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert "max-age=" in r.headers["strict-transport-security"]
    assert r.headers["cache-control"] == "no-store"


async def test_health_503_y_404_llevan_headers_de_seguridad():
    app = crear_app(pool=None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bot") as c:
        r_health = await c.get("/health")
        r_404 = await c.get("/ruta-inexistente")
    assert r_health.status_code == 503
    assert r_404.status_code == 404
    _seguros(r_health)
    _seguros(r_404)


async def test_webhook_acepta_un_update_normal_de_telegram(pool, datos_bot):
    cliente, _ = montar(pool)
    r = await enviar(cliente, datos_bot, update(texto="x" * 4000))
    assert r.status_code == 200
    _seguros(r)


async def test_webhook_rechaza_con_413_un_cuerpo_enorme(pool, datos_bot):
    cliente, _ = montar(pool)
    r = await cliente.post(
        RUTA,
        content=b"x" * (1024 * 1024 + 1),
        headers={CABECERA: datos_bot[1], "Content-Type": "application/json"},
    )
    assert r.status_code == 413
    _seguros(r)


async def test_500_no_manejado_lleva_headers_y_cuerpo_generico():
    app = crear_app(pool=None)

    @app.get("/ruta-que-explota")
    async def explota():
        raise RuntimeError("detalle-interno-secreto")

    transporte = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transporte, base_url="http://bot") as c:
        r = await c.get("/ruta-que-explota")
    assert r.status_code == 500
    _seguros(r)
    assert "server" not in r.headers
    assert "detalle-interno-secreto" not in r.text
    assert "Traceback" not in r.text
