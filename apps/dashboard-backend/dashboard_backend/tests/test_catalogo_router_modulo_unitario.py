"""El router de catálogo es un router de módulo y cada endpoint usa una sola transacción."""

import uuid

import pytest
from fastapi import APIRouter

from dashboard_backend import catalogo_asignaciones, catalogo_profesionales, catalogo_servicios, rutas_catalogo
from dashboard_backend.tests.soporte import ORIGEN, ConnFalsa, PoolFalso, app_de_prueba, http, token

TENANT = uuid.uuid4()
USUARIO = uuid.uuid4()
ID = uuid.uuid4()
SERVICIO = {"name": "Corte", "duration_minutes": 30, "price": 1000}


class ConnCatalogo(ConnFalsa):
    async def fetchrow(self, sql, *args):
        previo = await super().fetchrow(sql, *args)
        if previo is not None:
            return previo
        return {
            "id": ID,
            "price": 1000,
            "tenant_user_id": None,
            "confirmation_mode": "auto",
            "professional_id": ID,
            "service_id": ID,
        }

    async def fetchval(self, sql, *args):
        if "write_audit_log" in sql or "ping" in sql:
            return await super().fetchval(sql, *args)
        return 1 if "select 1" in sql else None


def test_el_router_es_de_modulo_con_las_mismas_rutas():
    assert isinstance(rutas_catalogo.router, APIRouter)
    rutas = {(m, r.path) for r in rutas_catalogo.router.routes for m in r.methods}
    assert rutas == {
        ("GET", "/servicios"),
        ("GET", "/servicios/{servicio_id}"),
        ("POST", "/servicios"),
        ("PATCH", "/servicios/{servicio_id}"),
        ("DELETE", "/servicios/{servicio_id}"),
        ("GET", "/profesionales"),
        ("GET", "/profesionales/{profesional_id}"),
        ("POST", "/profesionales"),
        ("PATCH", "/profesionales/{profesional_id}"),
        ("DELETE", "/profesionales/{profesional_id}"),
        ("GET", "/profesionales/{profesional_id}/servicios"),
        ("PUT", "/profesionales/{profesional_id}/servicios/{servicio_id}"),
        ("DELETE", "/profesionales/{profesional_id}/servicios/{servicio_id}"),
    }


@pytest.mark.parametrize(
    "metodo,ruta,cuerpo",
    [
        ("GET", "/servicios/" + str(ID), None),
        ("POST", "/servicios", SERVICIO),
        ("PATCH", f"/servicios/{ID}", {"price": 2000}),
        ("DELETE", f"/servicios/{ID}", None),
        ("GET", f"/profesionales/{ID}", None),
        ("POST", "/profesionales", {"name": "Ana"}),
        ("PATCH", f"/profesionales/{ID}", {"name": "Bea"}),
        ("DELETE", f"/profesionales/{ID}", None),
        ("GET", f"/profesionales/{ID}/servicios", None),
        ("PUT", f"/profesionales/{ID}/servicios/{ID}", {"confirmation_mode": "auto"}),
        ("DELETE", f"/profesionales/{ID}/servicios/{ID}", None),
    ],
)
async def test_cada_endpoint_abre_una_sola_transaccion(metodo, ruta, cuerpo):
    pool = PoolFalso()
    pool.conn = ConnCatalogo()
    app, _, _ = app_de_prueba(pool=pool)
    headers = {
        "Origin": ORIGEN,
        "Cookie": f"dash_acceso={token(user_id=USUARIO, tenant_id=TENANT, is_owner=True, aal='aal2')}",
    }
    r = await http(app).request(metodo, ruta, json=cuerpo, headers=headers)
    assert r.status_code < 300, r.text
    assert pool.adquisiciones == 1


async def test_el_dominio_lanza_errores_propios_cuando_no_existe():
    class ConnVacia:
        async def fetchrow(self, sql, *args):
            return None

        async def fetchval(self, sql, *args):
            return None

    with pytest.raises(catalogo_servicios.ErrorServicioInexistente):
        await catalogo_servicios.ver_servicio(ConnVacia(), TENANT, ID)
    with pytest.raises(catalogo_profesionales.ErrorProfesionalInexistente):
        await catalogo_profesionales.borrar_profesional(ConnVacia(), TENANT, ID)
    with pytest.raises(catalogo_asignaciones.ErrorAsignacionInexistente):
        await catalogo_asignaciones.quitar_servicio(ConnVacia(), TENANT, ID, ID)
    with pytest.raises(catalogo_profesionales.ErrorProfesionalInexistente):
        await catalogo_asignaciones.servicios_del_profesional(ConnVacia(), TENANT, ID)


def test_el_dominio_arma_el_update_parcial_con_parametros():
    sql, valores = catalogo_servicios.armar_update_servicio({"name": "x", "price": 5})
    assert "name = $3, price = $4" in sql
    assert valores == ["x", 5]
