"""Dependencias que combinan platform_auth + platform_tenancy, /health, observabilidad (sin base real)."""

import io
import json
import logging
import uuid
from typing import Annotated

import pytest
from fastapi import Depends, HTTPException
from observabilidad import configurar_logging
from platform_auth import VerificadorJWT
from pydantic import BaseModel

from dashboard_backend.app import crear_app
from dashboard_backend.dependencias import Transaccion
from dashboard_backend.tests.soporte import (
    AUD,
    SECRETO,
    PoolFalso,
    app_de_prueba,
    config_prueba,
    http,
    token,
)

TENANT = uuid.uuid4()
OTRO_TENANT = uuid.uuid4()


class _Cuerpo(BaseModel):
    tenant_id: str | None = None
    texto: str = ""


def _con_rutas(app):
    deps = app.state.dependencias

    @app.post("/prueba/tx")
    async def con_tx(cuerpo: _Cuerpo, tx: Annotated[Transaccion, Depends(deps.tx)]):
        return {"tenant": str(tx.sesion.tenant_id), "actor": str(tx.sesion.user_id)}

    @app.get("/prueba/reservas")
    async def con_permiso(tx: Annotated[Transaccion, Depends(deps.tx_con_permiso("view_bookings"))]):
        return {"ok": True}

    @app.post("/prueba/staff")
    async def solo_owner(tx: Annotated[Transaccion, Depends(deps.tx_solo_owner)]):
        return {"ok": True}

    @app.post("/prueba/falla")
    async def falla(tx: Annotated[Transaccion, Depends(deps.tx)]):
        raise HTTPException(status_code=409, detail="conflicto")

    @app.get("/prueba/log")
    async def log(tx: Annotated[Transaccion, Depends(deps.tx)]):
        logging.getLogger("prueba").info("dentro del endpoint")
        return {"ok": True}

    return app


def _auth(**kw):
    return {"Cookie": f"dash_acceso={token(**kw)}"}


async def test_la_transaccion_ya_trae_tenant_y_actor_seteados_desde_el_jwt():
    app, _, pool = app_de_prueba()
    _con_rutas(app)
    user = uuid.uuid4()
    r = await http(app).post(
        "/prueba/tx",
        json={"texto": "hola"},
        headers={"Origin": "https://panel.ejemplo.test", **_auth(user_id=user, tenant_id=TENANT)},
    )
    assert r.status_code == 200
    assert r.json() == {"tenant": str(TENANT), "actor": str(user)}
    sentencias = pool.conn.sentencias()
    assert "BEGIN" in sentencias and "COMMIT" in sentencias
    seteos = [a for s, a in pool.conn.ejecutado if "set_config('app.current_tenant_id'" in s]
    # el último seteo es el de la transacción del endpoint: tenant y actor del JWT
    assert seteos[-1] == (str(TENANT), str(user))


async def test_un_body_con_tenant_id_ajeno_se_ignora():
    app, _, pool = app_de_prueba()
    _con_rutas(app)
    r = await http(app).post(
        "/prueba/tx",
        json={"tenant_id": str(OTRO_TENANT)},
        headers={"Origin": "https://panel.ejemplo.test", **_auth(tenant_id=TENANT)},
    )
    assert r.status_code == 200
    assert r.json()["tenant"] == str(TENANT)
    todos = [a for s, a in pool.conn.ejecutado if "set_config" in s]
    assert all(str(OTRO_TENANT) not in a for a in todos)


async def test_un_query_param_con_tenant_id_ajeno_se_ignora():
    app, _, pool = app_de_prueba()
    _con_rutas(app)
    r = await http(app).post(
        f"/prueba/tx?tenant_id={OTRO_TENANT}",
        json={},
        headers={"Origin": "https://panel.ejemplo.test", **_auth(tenant_id=TENANT)},
    )
    assert r.json()["tenant"] == str(TENANT)


async def test_tenant_suspendido_es_403_en_un_endpoint_protegido():
    app, _, pool = app_de_prueba()
    _con_rutas(app)
    pool.conn.estado_tenant = {"status": "suspended", "is_owner": False, "is_active": True}
    r = await http(app).get("/prueba/reservas", headers=_auth(tenant_id=TENANT))
    assert r.status_code == 403
    assert r.json()["detail"]["codigo"] == "tenant_no_activo"


async def test_sin_el_permiso_es_403_y_con_el_permiso_pasa():
    app, _, pool = app_de_prueba()
    _con_rutas(app)
    pool.conn.estado_staff = {"is_owner": False, "is_active": True, "tiene": False}
    r = await http(app).get("/prueba/reservas", headers=_auth(tenant_id=TENANT))
    assert r.status_code == 403
    assert r.json()["detail"]["codigo"] == "permiso_denegado"
    assert "ROLLBACK" in pool.conn.sentencias()  # el chequeo corre dentro de la misma transacción

    pool.conn.estado_staff = {"is_owner": False, "is_active": True, "tiene": True}
    assert (await http(app).get("/prueba/reservas", headers=_auth(tenant_id=TENANT))).status_code == 200


async def test_staff_desactivado_es_403_y_el_owner_vivo_pasa_sin_permiso():
    app, _, pool = app_de_prueba()
    _con_rutas(app)
    pool.conn.estado_staff = {"is_owner": False, "is_active": False, "tiene": True}
    assert (await http(app).get("/prueba/reservas", headers=_auth(tenant_id=TENANT))).status_code == 403
    pool.conn.estado_staff = {"is_owner": True, "is_active": True, "tiene": False}
    pool.conn.estado_tenant = {"status": "active", "is_owner": True, "is_active": True}
    r = await http(app).get("/prueba/reservas", headers=_auth(tenant_id=TENANT, is_owner=True, aal="aal2"))
    assert r.status_code == 200


async def test_solo_owner_vivo_toca_el_endpoint_de_escalacion():
    app, _, pool = app_de_prueba()
    _con_rutas(app)
    h = {"Origin": "https://panel.ejemplo.test", **_auth(tenant_id=TENANT)}
    pool.conn.estado_staff = {"is_owner": False, "is_active": True, "tiene": False}
    r = await http(app).post("/prueba/staff", headers=h)
    assert r.status_code == 403
    assert r.json()["detail"]["codigo"] == "no_es_owner"


async def test_un_error_del_endpoint_hace_rollback():
    app, _, pool = app_de_prueba()
    _con_rutas(app)
    r = await http(app).post(
        "/prueba/falla", headers={"Origin": "https://panel.ejemplo.test", **_auth(tenant_id=TENANT)}
    )
    assert r.status_code == 409
    assert "ROLLBACK" in pool.conn.sentencias()
    assert pool.conn.sentencias().count("COMMIT") == 1  # solo el de la autenticación previa


async def test_sin_token_los_endpoints_protegidos_son_401_y_no_abren_transaccion():
    app, _, pool = app_de_prueba()
    _con_rutas(app)
    r = await http(app).get("/prueba/reservas")
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"
    assert pool.conn.ejecutado == []


def test_clave_de_permiso_invalida_falla_al_declarar_la_ruta():
    app, _, _ = app_de_prueba()
    with pytest.raises(ValueError):
        app.state.dependencias.tx_con_permiso("  ")


# ---------------------------------------------------------------- /health


async def test_health_200_si_la_base_responde():
    app, _, _ = app_de_prueba()
    r = await http(app).get("/health")
    assert r.status_code == 200
    assert r.json() == {"estado": "ok"}


async def test_health_503_si_la_base_falla_sin_filtrar_el_detalle():
    app, _, pool = app_de_prueba()
    pool.conn.ping_falla = True
    r = await http(app).get("/health")
    assert r.status_code == 503
    assert r.json() == {"estado": "no_disponible"}
    assert "detalle interno" not in r.text


async def test_health_503_si_no_hay_pool():
    app = crear_app(
        config_prueba(),
        verificador=VerificadorJWT(algoritmos=["HS256"], audiencia=AUD, secreto=SECRETO),
        pool=None,
        configurar_observabilidad=False,
    )
    r = await http(app).get("/health")  # sin lifespan: el pool no se creó
    assert r.status_code == 503


async def test_health_503_si_la_base_no_contesta_a_tiempo(monkeypatch):
    import asyncio

    app, _, pool = app_de_prueba()

    async def lento(*a, **k):
        await asyncio.sleep(5)

    pool.conn.fetchval = lento
    monkeypatch.setattr("dashboard_backend.app.TIMEOUT_HEALTH_SEG", 0.05)
    r = await http(app).get("/health")
    assert r.status_code == 503


# ---------------------------------------------------------------- observabilidad


async def test_los_logs_dentro_del_endpoint_llevan_tenant_id_y_actor_type_en_json():
    app, _, _ = app_de_prueba()
    _con_rutas(app)
    salida = io.StringIO()
    configurar_logging(stream=salida)
    try:
        r = await http(app).get("/prueba/log", headers=_auth(tenant_id=TENANT))
    finally:
        logging.getLogger().handlers = [
            h for h in logging.getLogger().handlers if not getattr(h, "_observabilidad_handler", False)
        ]
    assert r.status_code == 200
    registros = [json.loads(linea) for linea in salida.getvalue().splitlines() if linea.strip()]
    dentro = [x for x in registros if x["message"] == "dentro del endpoint"]
    assert dentro and dentro[0]["tenant_id"] == str(TENANT) and dentro[0]["actor_type"] == "tenant_user"


async def test_el_log_de_acceso_no_lleva_cookies_ni_tokens():
    app, _, _ = app_de_prueba()
    salida = io.StringIO()
    configurar_logging(stream=salida)
    try:
        acceso = token(tenant_id=TENANT)
        await http(app).get("/health", headers={"Cookie": f"dash_acceso={acceso}"})
    finally:
        logging.getLogger().handlers = [
            h for h in logging.getLogger().handlers if not getattr(h, "_observabilidad_handler", False)
        ]
    texto = salida.getvalue()
    assert "peticion" in texto and "/health" in texto
    assert acceso not in texto


def test_crear_app_inicia_sentry_y_logging_con_el_nombre_del_servicio(monkeypatch):
    llamadas = []
    monkeypatch.setattr("dashboard_backend.app.iniciar_sentry", lambda dsn, servicio: llamadas.append((dsn, servicio)))
    monkeypatch.setattr("dashboard_backend.app.configurar_logging", lambda *a, **k: llamadas.append("logging"))
    crear_app(
        config_prueba(sentry_dsn="https://clave@o0.ingest.sentry.io/1"),
        verificador=VerificadorJWT(algoritmos=["HS256"], audiencia=AUD, secreto=SECRETO),
        pool=PoolFalso(),
    )
    assert ("https://clave@o0.ingest.sentry.io/1", "dashboard-backend") in llamadas
    assert "logging" in llamadas
