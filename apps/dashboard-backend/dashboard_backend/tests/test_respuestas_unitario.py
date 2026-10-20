"""Cada endpoint de turnos, packs, clientes y disponibilidad declara su modelo de respuesta y el cuerpo no cambia.

Las pruebas de forma fijan, por endpoint, las claves de la respuesta y el tipo JSON de cada valor (`null` incluido):
si un modelo de respuesta pierde un campo, agrega uno o cambia un tipo, falla acá. Los campos internos
(`tenant_id`, canal del cliente, cuenta vinculada del profesional...) no salen en turnos, packs ni clientes.
"""

import uuid
from datetime import datetime, time, timedelta, timezone  # noqa: F401, UP017 (el entorno local puede ser 3.10)
from typing import get_args, get_origin

import pytest
from fastapi.routing import APIRoute
from pydantic import BaseModel
from turnos_queries import consultas as q

from dashboard_backend import disponibilidad as disp
from dashboard_backend import rutas_clientes, rutas_disponibilidad, rutas_turnos
from dashboard_backend.tests.soporte import ORIGEN, ConnFalsa, PoolFalso, app_de_prueba, http, token
from dashboard_backend.tests.soporte_clientes import CalendarioSimulado as CalendarioClientes
from dashboard_backend.tests.soporte_clientes import ConnClientes, app_clientes
from dashboard_backend.tests.soporte_turnos import INICIO, app_turnos, fila_turno

TENANT = uuid.uuid4()
STAFF = uuid.uuid4()
ID = uuid.uuid4()
OCULTOS = {"tenant_id", "vencido", "customer_channel", "customer_channel_user_id", "customer_deleted_at"}


def _tipo(valor):
    if valor is None:
        return "null"
    return {bool: "bool", int: "int", float: "float", str: "str", list: "list", dict: "dict"}[type(valor)]


def forma(valor):
    """Clave -> tipo JSON (anidando los objetos), para comparar la forma de una respuesta."""
    return {k: forma(v) if k == "calendario" else _tipo(v) for k, v in sorted(valor.items())}


def _h(escribe=True):
    h = {"Cookie": "dash_acceso=" + token(user_id=STAFF, tenant_id=TENANT, is_owner=True, aal="aal2")}
    if escribe:
        h["Origin"] = ORIGEN
    return h


# --- forma de las respuestas de turnos ---------------------------------------------------------------------

BASE_TURNO = {
    "id": "str",
    "end_customer_id": "str",
    "service_id": "str",
    "professional_id": "str",
    "start_at": "str",
    "end_at": "str",
    "status": "str",
    "deposit_status": "str",
    "booking_pack_id": "null",
    "pending_expires_at": "str",
    "customer_name": "str",
    "service_name": "str",
    "professional_name": "str",
    "sin_cliente_identificable": "bool",
}
FORMA_ITEM_LISTADO = {**BASE_TURNO, "created_at": "str"}
FORMA_DETALLE = {
    **BASE_TURNO,
    "created_at": "str",
    "google_calendar_event_id": "null",
    "customer_phone": "str",
    "service_duration_minutes": "int",
    "service_price": "float",
}
FORMA_CAMBIO = {
    **BASE_TURNO,
    "google_calendar_event_id": "null",
    "customer_phone": "str",
    "calendario": {"estado": "str"},
    "aviso": "str",
}


def _fila_listado():
    f = fila_turno(TENANT, created_at=INICIO)
    for k in ("vencido", "customer_channel", "customer_channel_user_id", "customer_deleted_at"):
        f.pop(k)
    for k in ("professional_auth_user_id", "google_calendar_event_id", "customer_phone"):
        f.pop(k)
    return f


def _fila_detalle():
    f = fila_turno(TENANT, created_at=INICIO, service_duration_minutes=30, service_price=1500.5)
    for k in ("vencido", "customer_channel", "customer_channel_user_id", "customer_deleted_at"):
        f.pop(k)
    f.pop("professional_auth_user_id")
    return f


async def test_forma_de_get_turnos():
    app, conn, _, _ = app_turnos()
    conn.listado = [_fila_listado()]
    r = await http(app).get("/turnos?desde=2030-03-01T00:00:00Z&hasta=2030-03-31T00:00:00Z", headers=_h(False))
    assert r.status_code == 200, r.text
    cuerpo = r.json()
    assert forma(cuerpo) == {"turnos": "list", "limite": "int", "offset": "int"}
    assert forma(cuerpo["turnos"][0]) == FORMA_ITEM_LISTADO
    assert cuerpo["turnos"][0]["start_at"] == INICIO.isoformat()  # "+00:00", no "Z": el texto de siempre


async def test_forma_de_get_turno_detalle():
    app, conn, _, _ = app_turnos()
    conn.detalle = _fila_detalle()
    r = await http(app).get(f"/turnos/{conn.detalle['id']}", headers=_h(False))
    assert r.status_code == 200, r.text
    assert forma(r.json()) == FORMA_DETALLE
    assert r.json()["service_price"] == 1500.5


async def test_forma_de_post_turnos():
    app, conn, _, _ = app_turnos()
    conn.datos_alta = {
        "duration_minutes": 30,
        "service_name": "Corte",
        "service_active": True,
        "professional_active": True,
        "ofrece": True,
        "customer_name": "Cliente de prueba",
        "customer_phone": "+5491155500101",
        "customer_deleted_at": None,
    }
    cuerpo = {
        "end_customer_id": str(uuid.uuid4()),
        "service_id": str(uuid.uuid4()),
        "professional_id": str(uuid.uuid4()),
        "start_at": INICIO.isoformat(),
    }
    r = await http(app).post("/turnos", json=cuerpo, headers=_h())
    assert r.status_code == 201, r.text
    assert forma(r.json()) == {**FORMA_CAMBIO, "pending_expires_at": "null", "google_calendar_event_id": "str"}


@pytest.mark.parametrize("accion", ["aprobar", "rechazar", "cancelar", "completar", "mover"])
async def test_forma_de_los_cambios_de_estado(accion):
    app, conn, _, _ = app_turnos()
    fila = fila_turno(TENANT, status="confirmed" if accion in ("completar", "mover") else "pending")
    conn.turnos[fila["id"]] = fila
    kw = {"json": {"start_at": (INICIO + timedelta(days=1)).isoformat()}} if accion == "mover" else {}
    r = await http(app).post(f"/turnos/{fila['id']}/{accion}", headers=_h(), **kw)
    assert r.status_code == 200, r.text
    # Sin vencimiento: al salir de pending se limpia. El evento de Calendar solo existe si se sincronizó.
    esperado = {**FORMA_CAMBIO, "pending_expires_at": "null"}
    esperado["google_calendar_event_id"] = "str" if accion in ("aprobar", "mover") else "null"
    assert forma(r.json()) == esperado
    assert not OCULTOS & r.json().keys()


async def test_forma_de_packs():
    app, conn, _, _ = app_turnos()
    conn.listado = [
        {
            "id": uuid.uuid4(),
            "tenant_id": TENANT,
            "end_customer_id": uuid.uuid4(),
            "service_id": uuid.uuid4(),
            "total_sessions": 5,
            "used_sessions": 1,
            "origin": "manual_grant",
            "payment_status": "not_required",
            "expires_at": None,
            "created_at": INICIO,
        }
    ]
    forma_pack = {
        "id": "str",
        "end_customer_id": "str",
        "service_id": "str",
        "total_sessions": "int",
        "used_sessions": "int",
        "origin": "str",
        "payment_status": "str",
        "expires_at": "null",
        "created_at": "str",
    }
    r = await http(app).get("/packs", headers=_h(False))
    assert forma(r.json()) == {"packs": "list", "limite": "int", "offset": "int"}
    assert forma(r.json()["packs"][0]) == forma_pack
    conn.cliente_pack = {"id": uuid.uuid4(), "deleted_at": None}
    cuerpo = {"end_customer_id": str(uuid.uuid4()), "service_id": str(uuid.uuid4()), "total_sessions": 5}
    r = await http(app).post("/packs", json=cuerpo, headers=_h())
    assert r.status_code == 201, r.text
    assert forma(r.json()) == forma_pack


# --- forma de las respuestas de clientes ----------------------------------------------------------------------

BASE_CLIENTE = {
    "id": "str",
    "channel": "null",
    "phone": "str",
    "name": "str",
    "deleted_at": "null",
    "created_at": "str",
    "sin_cliente_identificable": "bool",
}


class ConnClientesListado(ConnClientes):
    async def fetch(self, sql, *args):
        if sql == q.SQL_LISTAR_CLIENTES:
            self.ejecutado.append((sql, args))
            f = self._fila(phone="+5491155500101", name="Ana")
            f.pop("extra_data")  # la consulta del listado no selecciona extra_data
            return [f]
        return await super().fetch(sql, *args)


async def test_forma_de_los_endpoints_de_clientes():
    conn = ConnClientesListado()
    pool = PoolFalso()
    pool.conn = conn
    app, _, _ = app_clientes(pool=pool)
    conn.clientes[ID] = conn._fila(id=ID, phone="+5491155500102", name="Cai", extra_data='{"a": 1}')
    cliente = http(app)

    r = await cliente.get("/clientes", headers=_h(False))
    assert r.status_code == 200, r.text
    assert forma(r.json()) == {"clientes": "list", "limite": "int", "offset": "int"}
    assert forma(r.json()["clientes"][0]) == BASE_CLIENTE  # el listado no trae extra_data

    r = await cliente.get(f"/clientes/{ID}", headers=_h(False))
    assert forma(r.json()) == {**BASE_CLIENTE, "extra_data": "dict"}
    assert r.json()["extra_data"] == {"a": 1}

    r = await cliente.post("/clientes", json={"phone": "+54 9 11 5550-0101", "name": "Ana"}, headers=_h())
    assert r.status_code == 201, r.text
    assert forma(r.json()) == {**BASE_CLIENTE, "extra_data": "dict"}

    r = await cliente.patch(f"/clientes/{ID}", json={"name": "Bea"}, headers=_h())
    assert r.status_code == 200, r.text
    assert forma(r.json()) == {**BASE_CLIENTE, "extra_data": "dict"}
    assert "tenant_id" not in r.json()


async def test_forma_de_anonimizar():
    conn = ConnClientes()
    pool = PoolFalso()
    pool.conn = conn
    conn.clientes[ID] = conn._fila(id=ID, phone="+5491155500102", name="Cai")
    conn.targets = [{"integration_id": uuid.uuid4(), "google_calendar_event_id": "evt-1"}]
    app, _, _ = app_clientes(pool=pool, servicio=lambda c: CalendarioClientes(c))
    r = await http(app).post(f"/clientes/{ID}/anonimizar", headers=_h())
    assert r.status_code == 200, r.text
    assert forma(r.json()) == {
        "id": "str",
        "sin_cliente_identificable": "bool",
        "calendario": {"estado": "str", "eventos": "int", "no_limpiados": "int"},
    }


# --- forma de las respuestas de disponibilidad -----------------------------------------------------------------

FORMA_REGLA = {
    "id": "str",
    "tenant_id": "str",
    "professional_id": "str",
    "day_of_week": "int",
    "start_time": "str",
    "end_time": "str",
    "created_at": "str",
}
FORMA_BLOQUEO = {
    "id": "str",
    "tenant_id": "str",
    "professional_id": "null",
    "start_at": "str",
    "end_at": "str",
    "reason": "str",
    "created_at": "str",
}
REGLA_FILA = {
    "id": ID,
    "tenant_id": TENANT,
    "professional_id": ID,
    "day_of_week": 1,
    "start_time": time(9, 0),
    "end_time": time(18, 0),
    "created_at": INICIO,
}
BLOQUEO_FILA = {
    "id": ID,
    "tenant_id": TENANT,
    "professional_id": None,
    "start_at": INICIO,
    "end_at": INICIO + timedelta(days=1),
    "reason": "Feriado",
    "created_at": INICIO,
}


class ConnDisp(ConnFalsa):
    async def fetchrow(self, sql, *args):
        previo = await super().fetchrow(sql, *args)
        if previo is not None:
            return previo
        if "availability_rules" in sql:
            return REGLA_FILA
        if "availability_blocks" in sql:
            return BLOQUEO_FILA
        return None

    async def fetch(self, sql, *args):
        previo = await super().fetch(sql, *args)
        if previo:
            return previo
        if sql == disp.SQL_LISTAR_REGLAS:
            return [REGLA_FILA]
        if sql == disp.SQL_LISTAR_BLOQUEOS:
            return [BLOQUEO_FILA]
        return []


async def test_forma_de_los_endpoints_de_disponibilidad():
    pool = PoolFalso()
    pool.conn = ConnDisp()
    app = app_de_prueba(pool=pool)[0]
    cliente = http(app)
    regla = {"professional_id": str(ID), "day_of_week": 1, "start_time": "09:00", "end_time": "18:00"}
    bloqueo = {"start_at": "2026-12-24T00:00:00-03:00", "end_at": "2026-12-26T00:00:00-03:00", "reason": "Feriado"}
    casos = [
        ("GET", "/reglas-disponibilidad", None, 200, FORMA_REGLA, True),
        ("GET", f"/reglas-disponibilidad/{ID}", None, 200, FORMA_REGLA, False),
        ("POST", "/reglas-disponibilidad", regla, 201, FORMA_REGLA, False),
        ("PATCH", f"/reglas-disponibilidad/{ID}", {"day_of_week": 2}, 200, FORMA_REGLA, False),
        ("GET", "/bloqueos-disponibilidad", None, 200, FORMA_BLOQUEO, True),
        ("GET", f"/bloqueos-disponibilidad/{ID}", None, 200, FORMA_BLOQUEO, False),
        ("POST", "/bloqueos-disponibilidad", bloqueo, 201, FORMA_BLOQUEO, False),
        ("PATCH", f"/bloqueos-disponibilidad/{ID}", {"reason": "x"}, 200, FORMA_BLOQUEO, False),
    ]
    for metodo, ruta, cuerpo, estado, esperado, lista in casos:
        r = await cliente.request(metodo, ruta, json=cuerpo, headers=_h(metodo != "GET"))
        assert r.status_code == estado, (metodo, ruta, r.text)
        salida = r.json()
        if lista:
            assert isinstance(salida, list) and len(salida) == 1
            salida = salida[0]
        assert forma(salida) == esperado, (metodo, ruta)


# --- todo endpoint declara su modelo -------------------------------------------------------------------------------


def _es_modelo(tipo):
    if get_origin(tipo) is list:
        return _es_modelo(get_args(tipo)[0])
    return isinstance(tipo, type) and issubclass(tipo, BaseModel)


@pytest.mark.parametrize("router", [rutas_turnos.router, rutas_clientes.router, rutas_disponibilidad.router])
def test_todo_endpoint_con_cuerpo_declara_un_modelo_de_respuesta(router):
    for ruta in (r for r in router.routes if isinstance(r, APIRoute)):
        if ruta.status_code == 204:
            continue
        assert _es_modelo(ruta.response_model), f"{sorted(ruta.methods)} {ruta.path}: la respuesta no es un modelo"
