"""El router de clientes es un router de módulo y cada endpoint usa una transacción."""

import uuid

import pytest
from fastapi import APIRouter

from dashboard_backend import rutas_clientes
from dashboard_backend.tests.soporte import PoolFalso
from dashboard_backend.tests.soporte_clientes import ConnClientes, app_clientes
from dashboard_backend.tests.test_clientes_unitario import _h, http

ID = uuid.uuid4()


def test_el_router_es_de_modulo_con_las_mismas_rutas():
    assert isinstance(rutas_clientes.router, APIRouter)
    rutas = {(m, r.path) for r in rutas_clientes.router.routes for m in r.methods}
    assert rutas == {
        ("GET", "/clientes"),
        ("GET", "/clientes/{cliente_id}"),
        ("POST", "/clientes"),
        ("PATCH", "/clientes/{cliente_id}"),
        ("POST", "/clientes/{cliente_id}/anonimizar"),
    }


@pytest.mark.parametrize(
    "metodo,ruta,cuerpo",
    [
        ("GET", "/clientes", None),
        ("GET", f"/clientes/{ID}", None),
        ("POST", "/clientes", {"phone": "+54 9 11 5550-0101", "name": "Ana"}),
        ("PATCH", f"/clientes/{ID}", {"name": "Bea"}),
    ],
)
async def test_cada_endpoint_abre_una_sola_transaccion(metodo, ruta, cuerpo):
    conn = ConnClientes()
    conn.clientes[ID] = conn._fila(id=ID, phone="+5491155500102", name="Cai")
    pool = PoolFalso()
    pool.conn = conn
    app, _, _ = app_clientes(pool=pool)
    r = await http(app).request(metodo, ruta, json=cuerpo, headers=_h(escribe=True, is_owner=True, aal="aal2"))
    assert r.status_code < 300, r.text
    assert pool.adquisiciones == 1


async def test_anonimizar_usa_una_transaccion_para_la_sesion_y_el_borrado_y_otra_corta_para_calendar():
    conn = ConnClientes()
    conn.clientes[ID] = conn._fila(id=ID, phone="+5491155500102", name="Cai")
    pool = PoolFalso()
    pool.conn = conn
    app, _, _ = app_clientes(pool=pool)
    r = await http(app).post(f"/clientes/{ID}/anonimizar", headers=_h(escribe=True, is_owner=True, aal="aal2"))
    assert r.status_code == 200, r.text
    # 1 = sesión viva + permiso + anonimización; 2 = lectura corta de los eventos de Calendar, ya con COMMIT hecho.
    assert pool.adquisiciones == 2
