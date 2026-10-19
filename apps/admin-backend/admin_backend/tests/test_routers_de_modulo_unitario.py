"""Los routers de panel, visores e impersonación son de módulo y toman sus piezas de `app.state` en cada request."""

import uuid

from fastapi import APIRouter

from admin_backend import dependencias, rutas_impersonacion, rutas_panel, rutas_visores
from admin_backend.tests.soporte import PoolFalso, app_de_prueba, bearer, http

TENANT = uuid.UUID("00000000-0000-0000-0000-0000000000bb")


def test_cada_modulo_expone_su_router_y_sus_helpers_importables():
    for modulo in (rutas_panel, rutas_visores, rutas_impersonacion):
        assert isinstance(modulo.router, APIRouter)
        assert not [n for n in vars(modulo) if n.startswith("crear_router_")]
    assert callable(rutas_visores._respuesta_cadena)
    assert callable(rutas_impersonacion._vista)


def test_la_app_deja_pools_y_cliente_en_app_state():
    pool, ro = PoolFalso(), PoolFalso()
    app, _, _ = app_de_prueba(pool=pool, pool_readonly=ro)
    assert app.state.obtener_pool() is pool
    assert app.state.obtener_pool_readonly() is ro
    assert app.state.cliente_auth_admin is not None


async def test_el_gate_aal2_sigue_aplicando_en_los_tres_routers():
    app, _, _ = app_de_prueba()
    cabeceras = bearer(aal="aal1")
    async with http(app) as c:
        for ruta in (
            "/tenants",
            f"/tenants/{TENANT}/audit-log/verificar-cadena",
            f"/tenants/{TENANT}/impersonar/servicios",
        ):
            r = await c.get(ruta, headers=cabeceras)
            assert r.status_code in (401, 403), ruta


def test_las_dependencias_de_modulo_leen_app_state():
    class Req:
        class app:  # noqa: N801
            class state:  # noqa: N801
                dependencias = object()
                obtener_pool = staticmethod(lambda: 1)
                obtener_pool_readonly = staticmethod(lambda: 2)
                cliente_auth_admin = "cliente"

    assert dependencias.dependencias_de(Req) is Req.app.state.dependencias
    assert dependencias.pool_admin(Req)() == 1
    assert dependencias.pool_readonly(Req)() == 2
    assert dependencias.cliente_auth_admin_de(Req) == "cliente"
