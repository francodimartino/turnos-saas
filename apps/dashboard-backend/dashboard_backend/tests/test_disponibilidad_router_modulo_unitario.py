"""El router de disponibilidad es un router de módulo y cada endpoint usa una sola transacción."""

import uuid
from datetime import datetime

import pytest
from fastapi import APIRouter

from dashboard_backend import disponibilidad, rutas_disponibilidad
from dashboard_backend.tests.soporte import ORIGEN, ConnFalsa, PoolFalso, app_de_prueba, http, token

TENANT = uuid.uuid4()
USUARIO = uuid.uuid4()
ID = uuid.uuid4()
REGLA = {"professional_id": str(ID), "day_of_week": 1, "start_time": "09:00", "end_time": "18:00"}
INICIO = datetime.fromisoformat("2026-01-01T00:00:00+00:00")
BLOQUEO = {"start_at": "2026-12-24T00:00:00-03:00", "end_at": "2026-12-26T00:00:00-03:00", "reason": "Feriado"}

# Una fila con las columnas de una regla y de un bloqueo: cada endpoint toma las suyas y descarta el resto.
FILA = {
    "id": ID,
    "tenant_id": TENANT,
    "professional_id": ID,
    "day_of_week": 1,
    "start_time": datetime.fromisoformat("2026-01-01T09:00:00").time(),
    "end_time": datetime.fromisoformat("2026-01-01T18:00:00").time(),
    "start_at": INICIO,
    "end_at": INICIO,
    "reason": None,
    "created_at": INICIO,
}


class ConnDisponibilidad(ConnFalsa):
    async def fetchrow(self, sql, *args):
        previo = await super().fetchrow(sql, *args)
        if previo is not None:
            return previo
        return FILA

    async def fetch(self, sql, *args):
        self.ejecutado.append((sql, args))
        return []


def test_el_router_es_de_modulo_con_las_mismas_rutas():
    assert isinstance(rutas_disponibilidad.router, APIRouter)
    rutas = {(m, r.path) for r in rutas_disponibilidad.router.routes for m in r.methods}
    assert rutas == {
        ("GET", "/reglas-disponibilidad"),
        ("GET", "/reglas-disponibilidad/{regla_id}"),
        ("POST", "/reglas-disponibilidad"),
        ("PATCH", "/reglas-disponibilidad/{regla_id}"),
        ("DELETE", "/reglas-disponibilidad/{regla_id}"),
        ("GET", "/bloqueos-disponibilidad"),
        ("GET", "/bloqueos-disponibilidad/{bloqueo_id}"),
        ("POST", "/bloqueos-disponibilidad"),
        ("PATCH", "/bloqueos-disponibilidad/{bloqueo_id}"),
        ("DELETE", "/bloqueos-disponibilidad/{bloqueo_id}"),
    }


@pytest.mark.parametrize(
    "metodo,ruta,cuerpo",
    [
        ("GET", "/reglas-disponibilidad", None),
        ("GET", f"/reglas-disponibilidad/{ID}", None),
        ("POST", "/reglas-disponibilidad", REGLA),
        ("PATCH", f"/reglas-disponibilidad/{ID}", {"day_of_week": 2}),
        ("DELETE", f"/reglas-disponibilidad/{ID}", None),
        ("GET", "/bloqueos-disponibilidad", None),
        ("GET", f"/bloqueos-disponibilidad/{ID}", None),
        ("POST", "/bloqueos-disponibilidad", BLOQUEO),
        ("PATCH", f"/bloqueos-disponibilidad/{ID}", {"reason": "x"}),
        ("DELETE", f"/bloqueos-disponibilidad/{ID}", None),
    ],
)
async def test_cada_endpoint_abre_una_sola_transaccion(metodo, ruta, cuerpo):
    pool = PoolFalso()
    pool.conn = ConnDisponibilidad()
    app, _, _ = app_de_prueba(pool=pool)
    headers = {
        "Origin": ORIGEN,
        "Cookie": f"dash_acceso={token(user_id=USUARIO, tenant_id=TENANT, is_owner=True, aal='aal2')}",
    }
    r = await http(app).request(metodo, ruta, json=cuerpo, headers=headers)
    assert r.status_code < 300, r.text
    assert pool.adquisiciones == 1


def test_el_dominio_arma_el_update_del_bloqueo_conservando_el_rango_no_enviado():
    sql, valores = disponibilidad.armar_update_bloqueo({"reason": "x", "start_at": INICIO})
    assert "reason = $3" in sql
    assert "coalesce($4::timestamptz, lower(time_range))" in sql
    assert "coalesce($5::timestamptz, upper(time_range))" in sql
    assert valores == ["x", INICIO, None]


async def test_el_dominio_lanza_errores_propios_cuando_no_existe():
    class ConnVacia:
        async def fetchrow(self, sql, *args):
            return None

    with pytest.raises(disponibilidad.ErrorReglaInexistente):
        await disponibilidad.ver_regla(ConnVacia(), TENANT, ID)
    with pytest.raises(disponibilidad.ErrorBloqueoInexistente):
        await disponibilidad.borrar_bloqueo(ConnVacia(), TENANT, ID)
