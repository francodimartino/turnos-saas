"""/health acotado ante una base congelada y 503 `servicio_ocupado` ante acquire/query vencidos (sin DB real)."""

import time
import uuid
from typing import Annotated

import pytest
from fastapi import Depends
from platform_tenancy import crear_pool

from dashboard_backend.dependencias import Transaccion
from dashboard_backend.tests.soporte import PoolFalso, app_de_prueba, http, token

TENANT = uuid.UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")


async def test_health_503_en_menos_de_5s_con_un_servidor_que_nunca_responde(dsn_servidor_mudo):
    pool = await crear_pool(dsn_servidor_mudo, statement_cache_size=0, min_size=0, max_size=1)
    app, _, _ = app_de_prueba(pool=pool)
    inicio = time.monotonic()
    try:
        r = await http(app).get("/health")
    finally:
        pool._pool.terminate()
    assert r.status_code == 503
    assert time.monotonic() - inicio < 5


def _app_con_ruta(pool):
    app, _, _ = app_de_prueba(pool=pool)
    deps = app.state.dependencias

    @app.get("/prueba/datos")
    async def datos(tx: Annotated[Transaccion, Depends(deps.tx)]):
        return {"ok": True}

    return app


class _PoolAcquireVencido(PoolFalso):
    def acquire(self):
        raise TimeoutError


class _PoolQueryVencida(PoolFalso):
    def __init__(self):
        super().__init__()

        async def vencida(*a, **k):
            raise TimeoutError

        self.conn.fetchrow = vencida


@pytest.mark.parametrize("pool_cls", [_PoolAcquireVencido, _PoolQueryVencida])
async def test_acquire_o_query_vencidos_responden_503_servicio_ocupado(pool_cls):
    app = _app_con_ruta(pool_cls())
    r = await http(app).get("/prueba/datos", headers={"Cookie": f"dash_acceso={token(tenant_id=TENANT)}"})
    assert r.status_code == 503
    assert r.json()["detail"]["codigo"] == "servicio_ocupado"
