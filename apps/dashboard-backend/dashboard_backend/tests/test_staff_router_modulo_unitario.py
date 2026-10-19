"""El router de staff es un router de módulo y cada endpoint usa una sola transacción (la invitación, tres cortas)."""

import uuid
from contextlib import asynccontextmanager

import pytest
from fastapi import APIRouter

from dashboard_backend import rutas_staff, staff
from dashboard_backend.tests.soporte import ORIGEN, PoolFalso, http, token
from dashboard_backend.tests.soporte_staff import AuthAdminSimulado, ConnStaff, app_staff

TENANT = uuid.uuid4()
YO = uuid.uuid4()


class PoolContador(PoolFalso):
    """Cuenta las adquisiciones y cuántas conexiones están tomadas en cada momento."""

    def __init__(self, conn):
        super().__init__()
        self.conn = conn
        self.abiertas = 0

    @asynccontextmanager
    async def acquire(self):
        self.adquisiciones += 1
        self.abiertas += 1
        try:
            yield self.conn
        finally:
            self.abiertas -= 1


class AuthAdminQueMira(AuthAdminSimulado):
    def __init__(self, pool):
        super().__init__()
        self.pool = pool
        self.abiertas_al_llamar: list[int] = []

    async def invitar_por_email(self, email, nombre=None):
        self.abiertas_al_llamar.append(self.pool.abiertas)
        return await super().invitar_por_email(email, nombre)


def _headers():
    return {
        "Origin": ORIGEN,
        "Cookie": "dash_acceso=" + token(user_id=YO, tenant_id=TENANT, is_owner=True, aal="aal2"),
    }


def _conn_con_objetivo():
    conn = ConnStaff()
    conn.estado_staff = {"is_owner": True, "is_active": True, "tiene": True}
    objetivo = conn.agregar(is_owner=False, permisos=["view_bookings"])
    return conn, objetivo


def test_el_router_es_de_modulo_con_las_mismas_rutas():
    assert isinstance(rutas_staff.router, APIRouter)
    rutas = {(m, r.path) for r in rutas_staff.router.routes for m in r.methods}
    assert rutas == {
        ("GET", "/staff"),
        ("POST", "/staff/invitaciones"),
        ("PUT", "/staff/{staff_id}/permisos"),
        ("PUT", "/staff/{staff_id}/owner"),
        ("POST", "/staff/{staff_id}/desactivar"),
        ("POST", "/staff/{staff_id}/reactivar"),
    }


@pytest.mark.parametrize(
    "metodo,ruta,cuerpo",
    [
        ("GET", "/staff", None),
        ("PUT", "/staff/{id}/permisos", {"permission_keys": ["view_bookings", "manage_customers"]}),
        ("PUT", "/staff/{id}/owner", {"is_owner": True}),
        ("POST", "/staff/{id}/desactivar", None),
        ("POST", "/staff/{id}/reactivar", None),
    ],
)
async def test_cada_endpoint_abre_una_sola_transaccion(metodo, ruta, cuerpo):
    conn, objetivo = _conn_con_objetivo()
    pool = PoolContador(conn)
    app, _ = app_staff(pool=pool)
    r = await http(app).request(metodo, ruta.format(id=objetivo), json=cuerpo, headers=_headers())
    assert r.status_code < 300, r.text
    assert pool.adquisiciones == 1


async def test_invitar_no_retiene_ninguna_conexion_mientras_espera_a_la_api():
    conn, _ = _conn_con_objetivo()
    pool = PoolContador(conn)
    admin = AuthAdminQueMira(pool)
    app, _ = app_staff(pool=pool, cliente_admin=admin)
    r = await http(app).post(
        "/staff/invitaciones",
        json={"email": "a@ejemplo.invalid", "nombre": "Ana Pérez", "permission_keys": []},
        headers=_headers(),
    )
    assert r.status_code == 201, r.text
    assert admin.abiertas_al_llamar == [0]
    # sesión viva + validación previa + alta con auditoría: tres transacciones cortas, ninguna abierta a la vez
    assert pool.adquisiciones == 3
    assert pool.abiertas == 0


def test_el_cliente_admin_se_toma_de_app_state():
    admin = AuthAdminSimulado()
    app, _ = app_staff(cliente_admin=admin)
    assert app.state.cliente_admin() is admin


async def test_el_dominio_lanza_errores_propios_sin_http():
    class ConnVacia:
        async def fetchrow(self, sql, *args):
            return None

    class Sesion:
        tenant_id = TENANT
        user_id = YO

    with pytest.raises(staff.ErrorStaffInexistente):
        await staff.cambiar_owner(ConnVacia(), Sesion(), uuid.uuid4(), True)
