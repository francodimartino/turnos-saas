"""Dependencias de FastAPI: traducen los errores de autenticación a 401/403 con el header correcto."""

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

from platform_auth import VerificadorJWT, dependencia_admin, dependencia_dashboard  # noqa: E402

AUD = "authenticated"
SECRETO = secrets.token_urlsafe(48)  # efímero: se genera en cada corrida


class _Conn:
    def __init__(self, fila):
        self.fila = fila

    async def execute(self, sql, *args):
        pass

    async def fetchrow(self, sql, *args):
        return self.fila


class _Pool:
    def __init__(self, fila):
        self.fila = fila

    @asynccontextmanager
    async def acquire(self):
        yield _Conn(self.fila)


def _token(**extra):
    claims = {
        "sub": str(uuid.uuid4()),
        "aud": AUD,
        "exp": int(time.time()) + 300,
        "aal": "aal1",
        "tenant_id": str(uuid.uuid4()),
        "is_owner": False,
        "is_platform_admin": False,
    }
    claims.update(extra)
    return jwt.encode(claims, SECRETO, algorithm="HS256")


def _app(fila):
    verificador = VerificadorJWT(algoritmos=["HS256"], audiencia=AUD, secreto=SECRETO)
    pool = _Pool(fila)
    app = FastAPI()
    normal = dependencia_dashboard(verificador, lambda: pool)
    enrol = dependencia_dashboard(verificador, lambda: pool, enrollment=True)
    admin = dependencia_admin(verificador, lambda: pool)

    @app.get("/datos")
    async def datos(sesion: Annotated[object, Depends(normal)]):
        return {"tenant_id": str(sesion.tenant_id)}

    @app.post("/mfa/enrollment")
    async def enrolar(sesion: Annotated[object, Depends(enrol)]):
        return {"ok": True}

    @app.get("/admin")
    async def solo_admin(sesion: Annotated[object, Depends(admin)]):
        return {"ok": True}

    return TestClient(app)


def _h(token):
    return {"Authorization": f"Bearer {token}"}


ACTIVO = {"status": "active", "is_owner": False, "is_active": True}


def test_sin_header_es_401_con_www_authenticate():
    r = _app(ACTIVO).get("/datos")
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("header", ["Basic abc", "Bearer", "bearer", "Token xyz"])
def test_esquema_incorrecto_es_401(header):
    assert _app(ACTIVO).get("/datos", headers={"Authorization": header}).status_code == 401


def test_token_valido_pasa():
    assert _app(ACTIVO).get("/datos", headers=_h(_token())).status_code == 200


def test_esquema_bearer_no_distingue_mayusculas():
    assert _app(ACTIVO).get("/datos", headers={"Authorization": f"bearer {_token()}"}).status_code == 200


def test_tenant_suspendido_es_403():
    fila = {"status": "suspended", "is_owner": False, "is_active": True}
    r = _app(fila).get("/datos", headers=_h(_token()))
    assert r.status_code == 403
    assert r.json()["detail"]["codigo"] == "tenant_no_activo"


def test_owner_aal1_403_en_endpoint_comun_y_200_en_enrollment():
    app = _app({"status": "active", "is_owner": True, "is_active": True})
    token = _token(is_owner=True, aal="aal1")
    assert app.get("/datos", headers=_h(token)).status_code == 403
    assert app.post("/mfa/enrollment", headers=_h(token)).status_code == 200


def test_admin_requiere_claim_y_aal2():
    app = _app({"existe": True})
    assert app.get("/admin", headers=_h(_token(tenant_id=None))).status_code == 403
    assert app.get("/admin", headers=_h(_token(tenant_id=None, is_platform_admin=True))).status_code == 403
    ok = _token(tenant_id=None, is_platform_admin=True, aal="aal2")
    assert app.get("/admin", headers=_h(ok)).status_code == 200


def test_la_respuesta_de_error_no_filtra_detalles_del_token():
    r = _app(ACTIVO).get("/datos", headers=_h("a.b.c"))
    assert r.status_code == 401
    assert "a.b.c" not in r.text
