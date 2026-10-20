"""Catálogo, staff, settings, calendar y /auth/*: cada endpoint declara su modelo de respuesta y el cuerpo no cambia.

Los esperados son literales JSON escritos a mano, en el formato de siempre (fechas con `+00:00`, números sin comillas,
ids como texto): si un modelo pierde un campo, agrega uno, o cambia un tipo o un formato, falla acá. Ningún modelo de
respuesta tiene un campo de token: los tokens viajan solo en cookies.
"""

import datetime
import json
import uuid

import pytest
from fastapi.routing import APIRoute
from pydantic import BaseModel
from turnos_sesion import respuestas_auth

from dashboard_backend import (
    respuestas_calendar,
    respuestas_catalogo,
    respuestas_settings,
    respuestas_staff,
    rutas_calendar,
    rutas_catalogo,
    rutas_settings,
    rutas_staff,
)
from dashboard_backend.tests.soporte import ORIGEN, ConnFalsa, PoolFalso, app_de_prueba, http, token
from dashboard_backend.tests.soporte_calendar import app_calendar
from dashboard_backend.tests.soporte_staff import app_staff

TENANT = uuid.UUID("11111111-1111-4111-8111-111111111111")
STAFF = uuid.UUID("22222222-2222-4222-8222-222222222222")
ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
OTRO = uuid.UUID("44444444-4444-4444-8444-444444444444")
CREADO = datetime.datetime(2026, 1, 1, 12, 30, tzinfo=datetime.timezone.utc)  # noqa: UP017 (puede ser 3.10)


def _h(escribe=True, owner=True):
    h = {"Cookie": "dash_acceso=" + token(user_id=STAFF, tenant_id=TENANT, is_owner=owner, aal="aal2")}
    if escribe:
        h["Origin"] = ORIGEN
    return h


SERVICIO_FILA = {
    "id": ID,
    "tenant_id": TENANT,
    "name": "Corte",
    "duration_minutes": 30,
    "price": 2500.0,
    "deposit_amount": 500,
    "is_active": True,
    "created_at": CREADO,
}
SERVICIO_JSON = (
    '{"id": "33333333-3333-4333-8333-333333333333", "tenant_id": "11111111-1111-4111-8111-111111111111",'
    ' "name": "Corte", "duration_minutes": 30, "price": 2500.0, "deposit_amount": 500, "is_active": true,'
    ' "created_at": "2026-01-01T12:30:00+00:00"}'
)
PROFESIONAL_FILA = {
    "id": ID,
    "tenant_id": TENANT,
    "name": "Ana",
    "is_active": True,
    "tenant_user_id": OTRO,
    "created_at": CREADO,
}
PROFESIONAL_JSON = (
    '{"id": "33333333-3333-4333-8333-333333333333", "tenant_id": "11111111-1111-4111-8111-111111111111",'
    ' "name": "Ana", "is_active": true, "tenant_user_id": "44444444-4444-4444-8444-444444444444",'
    ' "created_at": "2026-01-01T12:30:00+00:00"}'
)
ASIGNACION_FILA = {"professional_id": ID, "service_id": OTRO, "confirmation_mode": "auto"}
ASIGNACION_JSON = (
    '{"professional_id": "33333333-3333-4333-8333-333333333333",'
    ' "service_id": "44444444-4444-4444-8444-444444444444", "confirmation_mode": "auto"}'
)


class ConnFormas(ConnFalsa):
    """ConnFalsa que responde con `fila` (una) y `filas` (varias) en cualquier consulta de datos."""

    def __init__(self):
        super().__init__()
        self.fila: dict | None = None
        self.filas: list[dict] = []

    async def fetchrow(self, sql, *args):
        previo = await super().fetchrow(sql, *args)
        return previo if previo is not None else self.fila

    async def fetch(self, sql, *args):
        self.ejecutado.append((sql, args))
        return self.filas

    async def fetchval(self, sql, *args):
        if "write_audit_log" in sql or "ping" in sql:
            return await super().fetchval(sql, *args)
        return 1 if "select 1" in sql else None


@pytest.fixture
def entorno():
    pool = PoolFalso()
    pool.conn = ConnFormas()
    app, _, _ = app_de_prueba(pool=pool)
    return app, pool.conn


# --- catalogo ----------------------------------------------------------------------------------------------------


async def test_forma_de_servicios(entorno):
    app, conn = entorno
    conn.filas = [SERVICIO_FILA]
    r = await http(app).get("/servicios", headers=_h(False))
    assert r.status_code == 200, r.text
    assert r.json() == json.loads("[" + SERVICIO_JSON + "]")
    assert '"price":2500.0' in r.text.replace(" ", "")  # número, no el texto "2500.00"
    assert "+00:00" in r.text and 'Z"' not in r.text

    conn.fila = SERVICIO_FILA
    for metodo, ruta, cuerpo, estado in [
        ("GET", f"/servicios/{ID}", None, 200),
        ("POST", "/servicios", {"name": "Corte", "duration_minutes": 30, "price": 2500}, 201),
        ("PATCH", f"/servicios/{ID}", {"price": 2500}, 200),
    ]:
        r = await http(app).request(metodo, ruta, json=cuerpo, headers=_h())
        assert r.status_code == estado, r.text
        assert r.json() == json.loads(SERVICIO_JSON)


async def test_el_precio_conserva_entero_o_decimal_como_siempre(entorno):
    app, conn = entorno
    conn.fila = {**SERVICIO_FILA, "price": 1500, "deposit_amount": None}
    r = await http(app).get(f"/servicios/{ID}", headers=_h(False))
    assert '"price":1500,' in r.text.replace(" ", "") and r.json()["deposit_amount"] is None
    conn.fila = {**SERVICIO_FILA, "price": 1500.5}
    r = await http(app).get(f"/servicios/{ID}", headers=_h(False))
    assert '"price":1500.5,' in r.text.replace(" ", "")


async def test_forma_de_profesionales(entorno):
    app, conn = entorno
    conn.filas = [PROFESIONAL_FILA]
    r = await http(app).get("/profesionales", headers=_h(False))
    assert r.json() == json.loads("[" + PROFESIONAL_JSON + "]")
    conn.fila = PROFESIONAL_FILA
    for metodo, ruta, cuerpo, estado in [
        ("GET", f"/profesionales/{ID}", None, 200),
        ("POST", "/profesionales", {"name": "Ana"}, 201),
        ("PATCH", f"/profesionales/{ID}", {"name": "Ana"}, 200),
    ]:
        r = await http(app).request(metodo, ruta, json=cuerpo, headers=_h())
        assert r.status_code == estado, r.text
        assert r.json() == json.loads(PROFESIONAL_JSON)
    conn.fila = {**PROFESIONAL_FILA, "tenant_user_id": None}
    r = await http(app).get(f"/profesionales/{ID}", headers=_h(False))
    assert r.json()["tenant_user_id"] is None


async def test_forma_de_las_asignaciones_de_servicios(entorno):
    app, conn = entorno
    conn.filas = [ASIGNACION_FILA]
    r = await http(app).get(f"/profesionales/{ID}/servicios", headers=_h(False))
    assert r.status_code == 200, r.text
    assert r.json() == json.loads("[" + ASIGNACION_JSON + "]")
    conn.fila = ASIGNACION_FILA
    r = await http(app).put(f"/profesionales/{ID}/servicios/{OTRO}", json={"confirmation_mode": "auto"}, headers=_h())
    assert r.status_code == 200, r.text
    assert r.json() == json.loads(ASIGNACION_JSON)


# --- settings ----------------------------------------------------------------------------------------------------


async def test_forma_de_settings(entorno):
    app, conn = entorno
    conn.fila = {"settings": {"direccion": "Calle 1", "booking_min_notice_minutes": 30, "x": None}}
    r = await http(app).get("/local/settings", headers=_h(False))
    assert r.status_code == 200, r.text
    assert r.json() == {"settings": {"direccion": "Calle 1", "booking_min_notice_minutes": 30, "x": None}}


# --- staff -------------------------------------------------------------------------------------------------------


def _como_owner(conn):
    conn.estado_staff = {"is_owner": True, "is_active": True, "tiene": True}


async def test_forma_de_staff():
    app, conn = app_staff()
    otro = conn.agregar(permisos=("view_bookings",))
    conn.identidades = {otro: {"email": "otro@ejemplo.invalid", "nombre": "Otra Persona"}}
    _como_owner(conn)
    r = await http(app).get("/staff", headers=_h(False))
    assert r.status_code == 200, r.text
    assert r.json() == {
        "staff": [
            {
                "id": str(otro),
                "nombre": "Otra Persona",
                "email": "otro@ejemplo.invalid",
                "is_owner": False,
                "is_active": True,
                "es_yo": False,
                "created_at": "2026-01-01T00:00:00+00:00",
                "permission_keys": ["view_bookings"],
            }
        ]
    }

    pendiente = conn.agregar(es_yo=None)  # invitación sin aceptar: la base devuelve `es_yo` null
    r = await http(app).get("/staff", headers=_h(False))
    assert [f["es_yo"] for f in r.json()["staff"] if f["id"] == str(pendiente)] == [None]

    r = await http(app).put(f"/staff/{otro}/permisos", json={"permission_keys": ["view_bookings"]}, headers=_h())
    assert r.json() == {"id": str(otro), "permission_keys": ["view_bookings"]}
    for ruta in (f"/staff/{otro}/desactivar", f"/staff/{otro}/reactivar"):
        r = await http(app).post(ruta, headers=_h())
        assert r.status_code == 200, r.text
        assert set(r.json()) == {"id", "is_owner", "is_active"}
    r = await http(app).put(f"/staff/{otro}/owner", json={"is_owner": True}, headers=_h())
    assert r.json() == {"id": str(otro), "is_owner": True, "is_active": True}


# --- calendar ----------------------------------------------------------------------------------------------------


async def test_forma_del_estado_y_la_desconexion_de_calendar():
    app, conn, _ = app_calendar()
    prof = conn.agregar_profesional(TENANT, vinculado_a=STAFF)
    conn.estado_staff = {"is_owner": False, "is_active": True, "tiene": False}
    r = await http(app).get(f"/profesionales/{prof}/calendar", headers=_h(False, owner=False))
    assert r.status_code == 200, r.text
    assert r.json() == {
        "profesional_id": str(prof),
        "conectado": False,
        "conectado_desde": None,
        "google_configurado": r.json()["google_configurado"],
    }
    assert isinstance(r.json()["google_configurado"], bool)

    conn.conectar(prof)
    r = await http(app).get(f"/profesionales/{prof}/calendar", headers=_h(False, owner=False))
    assert r.json()["conectado"] is True
    assert r.json()["conectado_desde"] == "2030-01-01T00:00:00+00:00"  # "+00:00", no "Z"

    r = await http(app).delete(f"/profesionales/{prof}/calendar", headers=_h(owner=False))
    assert r.status_code == 200, r.text
    assert r.json() == {"profesional_id": str(prof), "conectado": False, "habia_conexion": True}


# --- ningún modelo de respuesta trae un token ----------------------------------------------------------------------


def _modelos(modulo) -> list[type[BaseModel]]:
    return [
        m
        for m in vars(modulo).values()
        if isinstance(m, type) and issubclass(m, BaseModel) and m is not BaseModel and m.__module__ == modulo.__name__
    ]


def test_ningun_modelo_de_respuesta_lleva_tokens():
    modelos = [
        m
        for modulo in (respuestas_catalogo, respuestas_staff, respuestas_settings, respuestas_calendar, respuestas_auth)
        for m in _modelos(modulo)
    ]
    assert len(modelos) >= 15
    for modelo in modelos:
        for campo in modelo.model_fields:
            assert "token" not in campo and campo not in {"access_token", "refresh_token", "code", "state"}, (
                modelo.__name__,
                campo,
            )


def test_todo_endpoint_declara_su_modelo_de_respuesta():
    for router in (rutas_catalogo.router, rutas_staff.router, rutas_settings.router, rutas_calendar.router):
        for ruta in router.routes:
            assert isinstance(ruta, APIRoute)
            if ruta.status_code == 204 or ruta.path == "/calendar/callback" and "GET" in ruta.methods:
                continue
            assert ruta.response_model is not None, (ruta.methods, ruta.path)
