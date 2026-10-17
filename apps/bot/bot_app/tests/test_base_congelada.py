"""/health del bot acotado ante una base congelada (servidor TCP que acepta y nunca responde, sin DB real)."""

import time

import httpx
from platform_tenancy import crear_pool

from bot_app import crear_app


async def test_health_503_en_menos_de_5s_con_un_servidor_que_nunca_responde(dsn_servidor_mudo):
    pool = await crear_pool(dsn_servidor_mudo, statement_cache_size=0, min_size=0, max_size=1)
    app = crear_app(pool=pool)
    cliente = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bot.test")
    inicio = time.monotonic()
    try:
        r = await cliente.get("/health")
    finally:
        pool._pool.terminate()
    assert r.status_code == 503
    assert r.json() == {"estado": "no_disponible"}
    assert time.monotonic() - inicio < 5
