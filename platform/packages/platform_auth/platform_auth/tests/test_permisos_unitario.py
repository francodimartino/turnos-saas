"""`requiere_permiso` / `requiere_owner_vivo` con un pool simulado (la base real se prueba en integración)."""

import secrets
import time
import uuid
from contextlib import asynccontextmanager
from typing import Annotated

import jwt
import pytest

pytest.importorskip("fastapi")

from fastapi import Depends, FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from platform_auth import (  # noqa: E402
    NoEsOwner,
    PermisoDenegado,
    StaffInactivo,
    VerificadorJWT,
    dependencia_dashboard,
    requiere_owner_vivo,
    requiere_permiso,
    verificar_owner_vivo,
    verificar_permiso,
)
from platform_auth.sesion import SesionDashboard  # noqa: E402

AUD = "authenticated"
SECRETO = secrets.token_urlsafe(48)  # efímero: se genera en cada corrida


class _Conn:
    """Responde según el SQL: el estado del tenant (sesión) o la fila de permisos/owner."""

    def __init__(self, estado, permisos):
        self.estado = estado
        self.permisos = permisos
        self.consultas = []

    async def execute(self, sql, *args):
        pass

    async def fetchrow(self, sql, *args):
        self.consultas.append((sql, args))
        return self.estado if "from public.tenants" in sql else self.permisos


class _Pool:
    def __init__(self, estado, permisos):
        self.conn = _Conn(estado, permisos)

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


def _sesion(is_owner=False):
    return SesionDashboard(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), is_owner=is_owner, aal="aal1", identidad=None)


def _token(**extra):
    claims = {
        "sub": str(uuid.uuid4()),
        "aud": AUD,
        "exp": int(time.time()) + 300,
        "aal": "aal2",
        "tenant_id": str(uuid.uuid4()),
        "is_owner": False,
        "is_platform_admin": False,
    }
    claims.update(extra)
    return jwt.encode(claims, SECRETO, algorithm="HS256")


ACTIVO = {"status": "active", "is_owner": False, "is_active": True}


def _app(estado, permisos):
    verificador = VerificadorJWT(algoritmos=["HS256"], audiencia=AUD, secreto=SECRETO)
    pool = _Pool(estado, permisos)
    sesion = dependencia_dashboard(verificador, lambda: pool)
    app = FastAPI()
    con_permiso = requiere_permiso("view_bookings", dependencia_sesion=sesion, obtener_pool=lambda: pool)
    solo_owner = requiere_owner_vivo(dependencia_sesion=sesion, obtener_pool=lambda: pool)

    @app.get("/reservas")
    async def reservas(s: Annotated[object, Depends(con_permiso)]):
        return {"tenant_id": str(s.tenant_id)}

    @app.post("/staff")
    async def staff(s: Annotated[object, Depends(solo_owner)]):
        return {"ok": True}

    return TestClient(app), pool


def _h(**extra):
    return {"Authorization": f"Bearer {_token(**extra)}"}


def test_con_el_permiso_pasa():
    app, _ = _app(ACTIVO, {"is_owner": False, "is_active": True, "tiene": True})
    assert app.get("/reservas", headers=_h()).status_code == 200


def test_sin_el_permiso_es_403_con_codigo():
    app, _ = _app(ACTIVO, {"is_owner": False, "is_active": True, "tiene": False})
    r = app.get("/reservas", headers=_h())
    assert r.status_code == 403
    assert r.json()["detail"]["codigo"] == "permiso_denegado"


def test_owner_vivo_pasa_aunque_no_tenga_el_permiso_asignado():
    app, _ = _app(ACTIVO, {"is_owner": True, "is_active": True, "tiene": False})
    assert app.get("/reservas", headers=_h()).status_code == 200


def test_sin_fila_de_staff_es_403():
    app, _ = _app(ACTIVO, None)
    assert app.get("/reservas", headers=_h()).status_code == 403


def test_sin_token_es_401_y_no_toca_la_base():
    app, pool = _app(ACTIVO, {"is_owner": False, "is_active": True, "tiene": True})
    assert app.get("/reservas").status_code == 401
    assert pool.conn.consultas == []


def test_la_consulta_de_permiso_va_parametrizada_y_con_la_clave():
    app, pool = _app(ACTIVO, {"is_owner": False, "is_active": True, "tiene": True})
    app.get("/reservas", headers=_h())
    sql, args = pool.conn.consultas[-1]
    assert "tenant_user_permissions" in sql
    assert "view_bookings" in args
    assert "view_bookings" not in sql


def test_endpoint_solo_owner_rechaza_a_quien_no_es_owner_en_vivo():
    app, _ = _app(ACTIVO, {"is_owner": False, "is_active": True, "tiene": True})
    r = app.post("/staff", headers=_h())
    assert r.status_code == 403
    assert r.json()["detail"]["codigo"] == "no_es_owner"


def test_endpoint_solo_owner_acepta_al_owner_vivo():
    app, _ = _app(ACTIVO, {"is_owner": True, "is_active": True, "tiene": False})
    assert app.post("/staff", headers=_h()).status_code == 200


def test_el_claim_is_owner_del_token_no_alcanza():
    app, _ = _app(ACTIVO, {"is_owner": False, "is_active": True, "tiene": False})
    assert app.post("/staff", headers=_h(is_owner=True)).status_code == 403


@pytest.mark.parametrize("clave", ["", "  ", None, 5])
def test_clave_de_permiso_invalida_falla_al_armar_la_dependencia(clave):
    with pytest.raises(ValueError):
        requiere_permiso(clave, dependencia_sesion=lambda r: None, obtener_pool=lambda: None)


async def test_staff_inactivo_en_vivo_es_staff_inactivo():
    conn = _Conn(ACTIVO, {"is_owner": True, "is_active": False, "tiene": True})
    with pytest.raises(StaffInactivo):
        await verificar_permiso(conn, _sesion(), "view_bookings")
    with pytest.raises(StaffInactivo):
        await verificar_owner_vivo(conn, _sesion())


async def test_verificar_funciones_lanzan_los_errores_esperados():
    sin = _Conn(ACTIVO, {"is_owner": False, "is_active": True, "tiene": False})
    with pytest.raises(PermisoDenegado):
        await verificar_permiso(sin, _sesion(), "edit_prices")
    with pytest.raises(NoEsOwner):
        await verificar_owner_vivo(sin, _sesion())
    owner = _Conn(ACTIVO, {"is_owner": True, "is_active": True, "tiene": False})
    await verificar_permiso(owner, _sesion(), "edit_prices")
    await verificar_owner_vivo(owner, _sesion())
