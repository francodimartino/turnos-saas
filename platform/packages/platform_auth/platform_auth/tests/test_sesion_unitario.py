"""Capa de sesión: orden de chequeos y decisiones 401/403 (con un pool falso; la base real va en integración)."""

import time
import uuid
from contextlib import asynccontextmanager

import jwt
import pytest

from platform_auth import (
    MFARequerida,
    NoAutenticado,
    NoEsPlatformAdmin,
    StaffInactivo,
    TenantNoActivo,
    VerificadorJWT,
    autenticar_admin,
    autenticar_dashboard,
)

AUD = "authenticated"
SECRETO = "secreto-de-prueba-hs256-de-al-menos-32-bytes!!"


class _ConexionFalsa:
    def __init__(self, fila):
        self._fila = fila
        self.sql: list[str] = []

    async def execute(self, sql, *args):
        self.sql.append(sql)

    async def fetchrow(self, sql, *args):
        self.sql.append(sql)
        return self._fila


class _PoolFalso:
    def __init__(self, fila=None):
        self.conn = _ConexionFalsa(fila)
        self.adquisiciones = 0

    @asynccontextmanager
    async def acquire(self):
        self.adquisiciones += 1
        yield self.conn


@pytest.fixture
def verificador():
    return VerificadorJWT(algoritmos=["HS256"], audiencia=AUD, secreto=SECRETO)


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


def _fila(status="active", is_owner=False, is_active=True):
    return {"status": status, "is_owner": is_owner, "is_active": is_active}


async def test_token_invalido_es_401_sin_tocar_la_base(verificador):
    pool = _PoolFalso(_fila())
    with pytest.raises(NoAutenticado):
        await autenticar_dashboard("basura", verificador=verificador, pool=pool)
    assert pool.adquisiciones == 0


async def test_dashboard_sin_tenant_id_es_401_sin_tocar_la_base(verificador):
    pool = _PoolFalso(_fila())
    with pytest.raises(NoAutenticado) as e:
        await autenticar_dashboard(_token(tenant_id=None), verificador=verificador, pool=pool)
    assert e.value.status_code == 401
    assert pool.adquisiciones == 0


async def test_dashboard_staff_activo_pasa(verificador):
    pool = _PoolFalso(_fila())
    token = _token()
    sesion = await autenticar_dashboard(token, verificador=verificador, pool=pool)
    assert sesion.is_owner is False
    assert sesion.tenant_id is not None and sesion.user_id is not None


async def test_dashboard_owner_aal1_se_rechaza_antes_de_la_base(verificador):
    pool = _PoolFalso(_fila(is_owner=True))
    with pytest.raises(MFARequerida):
        await autenticar_dashboard(_token(is_owner=True, aal="aal1"), verificador=verificador, pool=pool)
    assert pool.adquisiciones == 0


async def test_dashboard_owner_aal1_pasa_en_enrollment(verificador):
    pool = _PoolFalso(_fila(is_owner=True))
    sesion = await autenticar_dashboard(
        _token(is_owner=True, aal="aal1"), verificador=verificador, pool=pool, enrollment=True
    )
    assert sesion.is_owner is True


async def test_dashboard_owner_aal2_pasa(verificador):
    pool = _PoolFalso(_fila(is_owner=True))
    sesion = await autenticar_dashboard(_token(is_owner=True, aal="aal2"), verificador=verificador, pool=pool)
    assert sesion.is_owner is True


async def test_dashboard_is_owner_sale_de_la_base_no_del_claim(verificador):
    # Claim false/aal1 pero la base dice owner: se exige MFA con el valor en vivo.
    pool = _PoolFalso(_fila(is_owner=True))
    with pytest.raises(MFARequerida):
        await autenticar_dashboard(_token(is_owner=False, aal="aal1"), verificador=verificador, pool=pool)


async def test_dashboard_owner_degradado_en_vivo_ya_no_es_owner(verificador):
    # Claim owner+aal2 pero la base dice que ya no lo es: la sesión no hereda el privilegio.
    pool = _PoolFalso(_fila(is_owner=False))
    sesion = await autenticar_dashboard(_token(is_owner=True, aal="aal2"), verificador=verificador, pool=pool)
    assert sesion.is_owner is False


@pytest.mark.parametrize("status", ["suspended", "inactive", "purged", "otro"])
async def test_dashboard_tenant_no_activo_es_403(verificador, status):
    with pytest.raises(TenantNoActivo) as e:
        await autenticar_dashboard(_token(), verificador=verificador, pool=_PoolFalso(_fila(status=status)))
    assert e.value.status_code == 403


async def test_dashboard_tenant_inexistente_es_403(verificador):
    with pytest.raises(TenantNoActivo):
        await autenticar_dashboard(_token(), verificador=verificador, pool=_PoolFalso(None))


async def test_dashboard_staff_desactivado_es_403(verificador):
    with pytest.raises(StaffInactivo):
        await autenticar_dashboard(_token(), verificador=verificador, pool=_PoolFalso(_fila(is_active=False)))


async def test_dashboard_sin_fila_de_staff_es_403(verificador):
    fila = {"status": "active", "is_owner": None, "is_active": None}
    with pytest.raises(StaffInactivo):
        await autenticar_dashboard(_token(), verificador=verificador, pool=_PoolFalso(fila))


async def test_dashboard_tenant_suspendido_con_staff_desactivado_informa_tenant(verificador):
    # Nada que distinga para el cliente por qué: ambos son 403; el orden es determinista (tenant primero).
    fila = _fila(status="suspended", is_active=False)
    with pytest.raises(TenantNoActivo):
        await autenticar_dashboard(_token(), verificador=verificador, pool=_PoolFalso(fila))


async def test_admin_sin_claim_es_403_sin_tocar_la_base(verificador):
    pool = _PoolFalso({"existe": True})
    with pytest.raises(NoEsPlatformAdmin) as e:
        await autenticar_admin(_token(is_platform_admin=False), verificador=verificador, pool=pool)
    assert e.value.status_code == 403
    assert pool.adquisiciones == 0


async def test_admin_aal1_es_403_mfa(verificador):
    with pytest.raises(MFARequerida):
        await autenticar_admin(
            _token(is_platform_admin=True, aal="aal1", tenant_id=None),
            verificador=verificador,
            pool=_PoolFalso({"existe": True}),
        )


async def test_admin_aal1_pasa_en_enrollment(verificador):
    sesion = await autenticar_admin(
        _token(is_platform_admin=True, aal="aal1", tenant_id=None),
        verificador=verificador,
        pool=_PoolFalso({"existe": True}),
        enrollment=True,
    )
    assert sesion.user_id is not None


async def test_admin_con_aal2_y_sin_tenant_id_pasa(verificador):
    # Franco no está en tenant_users: tenant_id null es lo normal para admin-backend.
    sesion = await autenticar_admin(
        _token(is_platform_admin=True, aal="aal2", tenant_id=None),
        verificador=verificador,
        pool=_PoolFalso({"existe": True}),
    )
    assert sesion.user_id is not None


async def test_admin_removido_de_platform_admins_en_vivo_es_403(verificador):
    with pytest.raises(NoEsPlatformAdmin):
        await autenticar_admin(
            _token(is_platform_admin=True, aal="aal2", tenant_id=None),
            verificador=verificador,
            pool=_PoolFalso(None),
        )
