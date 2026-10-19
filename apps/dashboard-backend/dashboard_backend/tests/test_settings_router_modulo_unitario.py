"""El router de settings es un router de módulo: sus piezas se inyectan por `app.state` o por `dependency_overrides`."""

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
from fastapi import APIRouter, FastAPI

from dashboard_backend import dependencias, rutas_settings, settings_local
from dashboard_backend.dependencias import Transaccion
from dashboard_backend.settings_local import ErrorLocalInexistente, ErrorSettingsDemasiadoGrandes

TENANT = uuid.uuid4()


class ConnSettings:
    def __init__(self, settings):
        self.settings = settings
        self.sentencias = []

    async def fetchrow(self, sql, *args):
        self.sentencias.append(sql)
        return {"settings": self.settings}


def _sesion():
    return SimpleNamespace(tenant_id=TENANT)


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://prueba")


def test_el_router_es_de_modulo_y_se_registra_con_include_router():
    assert isinstance(rutas_settings.router, APIRouter)
    rutas = {(m, r.path) for r in rutas_settings.router.routes for m in r.methods}
    assert rutas == {("GET", "/local/settings"), ("PATCH", "/local/settings")}


async def test_piezas_falsas_por_app_state():
    conn = ConnSettings({"a": 1})

    class DepsFalsas:
        async def identidad(self, request):
            return "identidad"

        def abrir_sesion(self, identidad):
            assert identidad == "identidad"

            @asynccontextmanager
            async def _tx():
                yield Transaccion(conn, _sesion())

            return _tx()

    app = FastAPI()
    app.state.dependencias = DepsFalsas()
    app.include_router(rutas_settings.router)
    r = await _cliente(app).get("/local/settings")
    assert r.status_code == 200
    assert r.json() == {"settings": {"a": 1}}


async def test_piezas_falsas_por_dependency_overrides():
    conn = ConnSettings('{"b": 2}')

    async def tx_falsa():
        yield Transaccion(conn, _sesion())

    app = FastAPI()
    app.include_router(rutas_settings.router)
    app.dependency_overrides[dependencias.transaccion] = tx_falsa
    r = await _cliente(app).get("/local/settings")
    assert r.json() == {"settings": {"b": 2}}


async def test_el_permiso_de_escritura_tambien_se_sobreescribe(monkeypatch):
    auditadas = []

    async def registrar_falso(conn, accion, tabla, id_, metadata):
        auditadas.append((accion, metadata))

    monkeypatch.setattr(settings_local, "registrar", registrar_falso)
    conn = ConnSettings({"viejo": 1})

    async def tx_falsa():
        yield Transaccion(conn, _sesion())

    app = FastAPI()
    app.include_router(rutas_settings.router)
    app.dependency_overrides[rutas_settings.tx_gestiona_agenda] = tx_falsa
    r = await _cliente(app).patch("/local/settings", json={"settings": {"x": "y"}})
    assert r.status_code == 200
    assert r.json() == {"settings": {"viejo": 1}}
    assert auditadas == [("settings_updated", {"claves": ["x"]})]


def test_mezclar_reemplaza_y_elimina_con_none():
    assert settings_local.mezclar({"a": 1, "b": 2}, {"a": 3, "b": None, "c": 4}) == {"a": 3, "c": 4}


def test_mezclar_rechaza_un_resultado_sobre_el_tope():
    with pytest.raises(ErrorSettingsDemasiadoGrandes):
        settings_local.mezclar({}, {"k": "x" * (settings_local.MAX_BYTES_SETTINGS + 1)})


async def test_leer_de_un_local_inexistente_lanza_error_de_dominio():
    class ConnVacia:
        async def fetchrow(self, sql, *args):
            return None

    with pytest.raises(ErrorLocalInexistente):
        await settings_local.leer_settings(ConnVacia(), TENANT)
