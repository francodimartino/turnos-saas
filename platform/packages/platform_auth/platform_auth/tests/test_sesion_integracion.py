"""Integración de platform_auth contra Postgres real: el estado del tenant y del staff se lee EN VIVO."""

import time
import uuid
from urllib.parse import quote, urlsplit, urlunsplit

import jwt
import pytest
from platform_tenancy import crear_pool

from platform_auth import (
    MFARequerida,
    NoEsPlatformAdmin,
    StaffInactivo,
    TenantNoActivo,
    VerificadorJWT,
    autenticar_admin,
    autenticar_dashboard,
)

pytestmark = pytest.mark.integracion

pytest.importorskip("asyncpg")

AUD = "authenticated"
SECRETO = "secreto-de-integracion-hs256-de-al-menos-32-bytes"


def _dsn(base: str, rol: str, password: str) -> str:
    p = urlsplit(base)
    host = p.hostname or "127.0.0.1"
    if ":" in host:
        host = f"[{host}]"
    netloc = f"{quote(rol, safe='')}:{quote(password, safe='')}@{host}"
    if p.port:
        netloc += f":{p.port}"
    return urlunsplit((p.scheme, netloc, p.path, p.query, ""))


@pytest.fixture
async def pool_como(database_url, roles_password):
    pools = []

    async def _crear(rol: str):
        pool = await crear_pool(_dsn(database_url, rol, roles_password), min_size=1, max_size=1, statement_cache_size=0)
        pools.append(pool)
        return pool

    try:
        yield _crear
    finally:
        for pool in pools:
            await pool.close()


@pytest.fixture
def verificador():
    return VerificadorJWT(algoritmos=["HS256"], audiencia=AUD, secreto=SECRETO)


def _token(user_id, tenant_id, *, is_owner=False, is_platform_admin=False, aal="aal1"):
    return jwt.encode(
        {
            "sub": str(user_id),
            "aud": AUD,
            "exp": int(time.time()) + 600,
            "aal": aal,
            "tenant_id": None if tenant_id is None else str(tenant_id),
            "is_owner": is_owner,
            "is_platform_admin": is_platform_admin,
        },
        SECRETO,
        algorithm="HS256",
    )


async def _crear_usuario_auth(conn, user_id):
    await conn.execute(
        "insert into auth.users (id, email) values ($1, $2)", user_id, f"{user_id.hex[:12]}@prueba.invalid"
    )


async def _borrar(conn, sql, *args):
    """DELETE de limpieza sin triggers: las invariantes de último owner/último admin lo frenarían."""
    await conn.execute("set session_replication_role = replica")
    try:
        await conn.execute(sql, *args)
    finally:
        await conn.execute("set session_replication_role = origin")


@pytest.fixture
async def escenario(db_superuser):
    """Un tenant activo con un owner y un staff, más un segundo tenant con otro staff."""
    sufijo = uuid.uuid4().hex[:8]
    tenant, otro_tenant = uuid.uuid4(), uuid.uuid4()
    owner, staff, ajeno = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await db_superuser.execute(
        "insert into public.tenants (id, name, slug) values ($1, 'A', $2), ($3, 'B', $4)",
        tenant, f"it-auth-a-{sufijo}", otro_tenant, f"it-auth-b-{sufijo}",
    )
    for u in (owner, staff, ajeno):
        await _crear_usuario_auth(db_superuser, u)
    await db_superuser.execute(
        "insert into public.tenant_users (tenant_id, auth_user_id, is_owner) values "
        "($1, $2, true), ($1, $3, false), ($4, $5, false)",
        tenant, owner, staff, otro_tenant, ajeno,
    )
    try:
        yield {"tenant": tenant, "otro_tenant": otro_tenant, "owner": owner, "staff": staff, "ajeno": ajeno}
    finally:
        ambos = [tenant, otro_tenant]
        await _borrar(db_superuser, "delete from public.tenant_users where tenant_id = any($1::uuid[])", ambos)
        await _borrar(db_superuser, "delete from public.tenants where id = any($1::uuid[])", [tenant, otro_tenant])
        await _borrar(db_superuser, "delete from auth.users where id = any($1::uuid[])", [owner, staff, ajeno])


async def test_suspender_el_tenant_entre_dos_requests_con_el_mismo_token_da_403(
    escenario, verificador, pool_como, db_superuser
):
    pool = await pool_como("dashboard_service")
    token = _token(escenario["staff"], escenario["tenant"])

    sesion = await autenticar_dashboard(token, verificador=verificador, pool=pool)
    assert sesion.tenant_id == escenario["tenant"]

    await db_superuser.execute("update public.tenants set status = 'suspended' where id = $1", escenario["tenant"])
    with pytest.raises(TenantNoActivo) as e:
        await autenticar_dashboard(token, verificador=verificador, pool=pool)
    assert e.value.status_code == 403

    # Reactivado, el mismo token vuelve a servir: nada quedó cacheado.
    await db_superuser.execute("update public.tenants set status = 'active' where id = $1", escenario["tenant"])
    assert await autenticar_dashboard(token, verificador=verificador, pool=pool)


async def test_tenant_inactive_es_403(escenario, verificador, pool_como, db_superuser):
    pool = await pool_como("dashboard_service")
    await db_superuser.execute(
        "update public.tenants set status = 'inactive', deactivated_at = now() where id = $1", escenario["tenant"]
    )
    with pytest.raises(TenantNoActivo):
        await autenticar_dashboard(
            _token(escenario["staff"], escenario["tenant"]), verificador=verificador, pool=pool
        )


async def test_staff_desactivado_entre_dos_requests_da_403(escenario, verificador, pool_como, db_superuser):
    pool = await pool_como("dashboard_service")
    token = _token(escenario["staff"], escenario["tenant"])
    assert await autenticar_dashboard(token, verificador=verificador, pool=pool)

    await db_superuser.execute(
        "update public.tenant_users set is_active = false where auth_user_id = $1", escenario["staff"]
    )
    with pytest.raises(StaffInactivo) as e:
        await autenticar_dashboard(token, verificador=verificador, pool=pool)
    assert e.value.status_code == 403


async def test_usuario_con_tenant_id_de_otro_tenant_en_el_token_es_403(escenario, verificador, pool_como):
    # Defensa en profundidad: aunque el claim dijera otro tenant, la base no encuentra al usuario ahí.
    pool = await pool_como("dashboard_service")
    token = _token(escenario["ajeno"], escenario["tenant"])
    with pytest.raises(StaffInactivo):
        await autenticar_dashboard(token, verificador=verificador, pool=pool)


async def test_owner_en_vivo_exige_aal2_aunque_el_claim_diga_que_no(escenario, verificador, pool_como):
    pool = await pool_como("dashboard_service")
    # El claim dice is_owner=false, pero la base dice que sí lo es.
    token = _token(escenario["owner"], escenario["tenant"], is_owner=False, aal="aal1")
    with pytest.raises(MFARequerida):
        await autenticar_dashboard(token, verificador=verificador, pool=pool)
    sesion = await autenticar_dashboard(token, verificador=verificador, pool=pool, enrollment=True)
    assert sesion.is_owner is True


async def test_owner_degradado_en_vivo_pierde_el_privilegio(escenario, verificador, pool_como, db_superuser):
    pool = await pool_como("dashboard_service")
    token = _token(escenario["owner"], escenario["tenant"], is_owner=True, aal="aal2")
    assert (await autenticar_dashboard(token, verificador=verificador, pool=pool)).is_owner is True
    # Hay otro owner? No: la base impide quitar al último. Se agrega uno y se degrada al primero.
    otro = uuid.uuid4()
    await _crear_usuario_auth(db_superuser, otro)
    try:
        await db_superuser.execute(
            "insert into public.tenant_users (tenant_id, auth_user_id, is_owner) values ($1, $2, true)",
            escenario["tenant"], otro,
        )
        await db_superuser.execute(
            "update public.tenant_users set is_owner = false where auth_user_id = $1", escenario["owner"]
        )
        assert (await autenticar_dashboard(token, verificador=verificador, pool=pool)).is_owner is False
    finally:
        await _borrar(db_superuser, "delete from public.tenant_users where auth_user_id = $1", otro)
        await _borrar(db_superuser, "delete from auth.users where id = $1", otro)


async def test_admin_se_verifica_en_vivo_contra_platform_admins(verificador, pool_como, db_superuser):
    pool = await pool_como("platform_admin")
    franco = uuid.uuid4()
    await _crear_usuario_auth(db_superuser, franco)
    token = _token(franco, None, is_platform_admin=True, aal="aal2")
    try:
        # Claim true pero sin fila en platform_admins: 403.
        with pytest.raises(NoEsPlatformAdmin):
            await autenticar_admin(token, verificador=verificador, pool=pool)

        await db_superuser.execute("insert into public.platform_admins (auth_user_id) values ($1)", franco)
        sesion = await autenticar_admin(token, verificador=verificador, pool=pool)
        assert sesion.user_id == franco

        # Aal1 solo hacia el enrollment.
        aal1 = _token(franco, None, is_platform_admin=True, aal="aal1")
        with pytest.raises(MFARequerida):
            await autenticar_admin(aal1, verificador=verificador, pool=pool)
        assert await autenticar_admin(aal1, verificador=verificador, pool=pool, enrollment=True)

        # Removido entre requests: el mismo token deja de servir.
        await _borrar(db_superuser, "delete from public.platform_admins where auth_user_id = $1", franco)
        with pytest.raises(NoEsPlatformAdmin):
            await autenticar_admin(token, verificador=verificador, pool=pool)
    finally:
        await _borrar(db_superuser, "delete from public.platform_admins where auth_user_id = $1", franco)
        await _borrar(db_superuser, "delete from auth.users where id = $1", franco)
