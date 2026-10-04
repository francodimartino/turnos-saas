"""Permisos, escalación de privilegios e invitaciones contra Postgres real (§2.4, §2.9, §3.1.5)."""

import secrets
import time
import uuid
from typing import Annotated
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
import jwt
import pytest
from platform_tenancy import crear_pool

from platform_auth import (
    ErrorInvitacion,
    NoEsOwner,
    StaffYaExiste,
    VerificadorJWT,
    dependencia_dashboard,
    invitar_staff,
    requiere_owner_vivo,
    requiere_permiso,
)
from platform_auth.sesion import SesionDashboard

pytestmark = pytest.mark.integracion

pytest.importorskip("asyncpg")
pytest.importorskip("fastapi")

from fastapi import Depends, FastAPI  # noqa: E402

AUD = "authenticated"
SECRETO = secrets.token_urlsafe(48)  # efímero: se genera en cada corrida


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
async def pool(database_url, roles_password):
    pool = await crear_pool(
        _dsn(database_url, "dashboard_service", roles_password), min_size=1, max_size=2, statement_cache_size=0
    )
    try:
        yield pool
    finally:
        await pool.close()


def _token(user_id, tenant_id, *, is_owner=False, aal="aal2"):
    return jwt.encode(
        {
            "sub": str(user_id),
            "aud": AUD,
            "exp": int(time.time()) + 600,
            "aal": aal,
            "tenant_id": str(tenant_id),
            "is_owner": is_owner,
            "is_platform_admin": False,
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
    """Un tenant activo con un owner, un staff con `manage_staff` y un staff con `view_bookings`."""
    sufijo = uuid.uuid4().hex[:8]
    tenant = uuid.uuid4()
    owner, gerente, mozo = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await db_superuser.execute(
        "insert into public.tenants (id, name, slug) values ($1, 'A', $2)", tenant, f"it-perm-{sufijo}"
    )
    for u in (owner, gerente, mozo):
        await _crear_usuario_auth(db_superuser, u)
    ids = {}
    for nombre, u, es_owner in (("owner", owner, True), ("gerente", gerente, False), ("mozo", mozo, False)):
        ids[nombre] = await db_superuser.fetchval(
            "insert into public.tenant_users (tenant_id, auth_user_id, is_owner) values ($1, $2, $3) returning id",
            tenant, u, es_owner,
        )
    await db_superuser.execute(
        "insert into public.tenant_user_permissions (tenant_user_id, permission_key) values ($1, 'manage_staff')",
        ids["gerente"],
    )
    await db_superuser.execute(
        "insert into public.tenant_user_permissions (tenant_user_id, permission_key) values ($1, 'view_bookings')",
        ids["mozo"],
    )
    extra: list[uuid.UUID] = []
    try:
        yield {"tenant": tenant, "owner": owner, "gerente": gerente, "mozo": mozo, "ids": ids, "extra": extra}
    finally:
        await _borrar(db_superuser, "delete from public.tenant_user_permissions where tenant_id = $1", tenant)
        await _borrar(db_superuser, "delete from public.audit_log where tenant_id = $1", tenant)
        await _borrar(db_superuser, "delete from public.tenant_users where tenant_id = $1", tenant)
        await _borrar(db_superuser, "delete from public.tenants where id = $1", tenant)
        await _borrar(db_superuser, "delete from auth.users where id = any($1::uuid[])", [owner, gerente, mozo, *extra])


@pytest.fixture
def app_http(pool):
    verificador = VerificadorJWT(algoritmos=["HS256"], audiencia=AUD, secreto=SECRETO)
    sesion = dependencia_dashboard(verificador, lambda: pool)
    con_permiso = requiere_permiso("view_bookings", dependencia_sesion=sesion, obtener_pool=lambda: pool)
    solo_owner = requiere_owner_vivo(dependencia_sesion=sesion, obtener_pool=lambda: pool)
    app = FastAPI()

    @app.get("/reservas")
    async def reservas(s: Annotated[object, Depends(con_permiso)]):
        return {"ok": True}

    @app.post("/staff/permisos")
    async def permisos(s: Annotated[object, Depends(solo_owner)]):
        return {"ok": True}

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://prueba")


def _h(token):
    return {"Authorization": f"Bearer {token}"}


async def test_revocar_el_permiso_corta_el_acceso_en_el_siguiente_request(escenario, app_http, db_superuser):
    token = _token(escenario["mozo"], escenario["tenant"])
    assert (await app_http.get("/reservas", headers=_h(token))).status_code == 200

    await db_superuser.execute(
        "delete from public.tenant_user_permissions where tenant_user_id = $1", escenario["ids"]["mozo"]
    )
    r = await app_http.get("/reservas", headers=_h(token))  # mismo token
    assert r.status_code == 403
    assert r.json()["detail"]["codigo"] == "permiso_denegado"

    await db_superuser.execute(
        "insert into public.tenant_user_permissions (tenant_user_id, permission_key) values ($1, 'view_bookings')",
        escenario["ids"]["mozo"],
    )
    assert (await app_http.get("/reservas", headers=_h(token))).status_code == 200


async def test_staff_sin_ese_permiso_es_403_y_el_owner_pasa_sin_filas_de_permisos(escenario, app_http):
    gerente = _token(escenario["gerente"], escenario["tenant"])  # tiene manage_staff, no view_bookings
    assert (await app_http.get("/reservas", headers=_h(gerente))).status_code == 403
    owner = _token(escenario["owner"], escenario["tenant"], is_owner=True)
    assert (await app_http.get("/reservas", headers=_h(owner))).status_code == 200


async def test_staff_desactivado_pierde_el_permiso_en_el_acto(escenario, app_http, db_superuser):
    token = _token(escenario["mozo"], escenario["tenant"])
    assert (await app_http.get("/reservas", headers=_h(token))).status_code == 200
    await db_superuser.execute(
        "update public.tenant_users set is_active = false where id = $1", escenario["ids"]["mozo"]
    )
    assert (await app_http.get("/reservas", headers=_h(token))).status_code == 403


async def test_manage_staff_sin_is_owner_recibe_403_en_el_chequeo_de_escalacion(escenario, app_http):
    gerente = _token(escenario["gerente"], escenario["tenant"])
    r = await app_http.post("/staff/permisos", headers=_h(gerente))
    assert r.status_code == 403
    assert r.json()["detail"]["codigo"] == "no_es_owner"
    # Aunque el token afirme is_owner=true, manda la base.
    mentiroso = _token(escenario["gerente"], escenario["tenant"], is_owner=True)
    assert (await app_http.post("/staff/permisos", headers=_h(mentiroso))).status_code == 403
    owner = _token(escenario["owner"], escenario["tenant"], is_owner=True)
    assert (await app_http.post("/staff/permisos", headers=_h(owner))).status_code == 200


async def test_owner_degradado_en_la_base_pierde_el_poder_aunque_su_jwt_diga_owner(
    escenario, app_http, db_superuser
):
    token = _token(escenario["owner"], escenario["tenant"], is_owner=True)
    assert (await app_http.post("/staff/permisos", headers=_h(token))).status_code == 200

    # La base impide dejar al tenant sin owner: se asciende a otro y se degrada al primero.
    await db_superuser.execute(
        "update public.tenant_users set is_owner = true where id = $1", escenario["ids"]["gerente"]
    )
    await db_superuser.execute(
        "update public.tenant_users set is_owner = false where id = $1", escenario["ids"]["owner"]
    )
    r = await app_http.post("/staff/permisos", headers=_h(token))  # mismo token, is_owner=true en el claim
    assert r.status_code == 403
    assert r.json()["detail"]["codigo"] == "no_es_owner"


# --- invitaciones -----------------------------------------------------------------------------------------


class _ClienteAuth:
    """Cliente simulado: crea la cuenta en `auth.users` como lo haría Supabase y falla bajo demanda."""

    def __init__(self, conn, escenario):
        self.conn = conn
        self.escenario = escenario
        self.fallos = 0
        self.cuentas: dict[str, uuid.UUID] = {}
        self.llamadas = 0

    async def invitar_por_email(self, email):
        self.llamadas += 1
        if self.fallos:
            self.fallos -= 1
            raise ErrorInvitacion("la API falló", reintentable=True)
        if email not in self.cuentas:
            nuevo = uuid.uuid4()
            await self.conn.execute("insert into auth.users (id, email) values ($1, $2)", nuevo, email)
            self.cuentas[email] = nuevo
            self.escenario["extra"].append(nuevo)
        return self.cuentas[email]


def _sesion_de(escenario, quien, *, is_owner):
    return SesionDashboard(
        user_id=escenario[quien], tenant_id=escenario["tenant"], is_owner=is_owner, aal="aal2", identidad=None
    )


async def _contar(db, escenario):
    return await db.fetchval("select count(*) from public.tenant_users where tenant_id = $1", escenario["tenant"])


async def test_owner_invita_staff_con_permisos_y_queda_auditado(escenario, pool, db_superuser):
    cliente = _ClienteAuth(db_superuser, escenario)
    email = f"{uuid.uuid4().hex[:10]}@prueba.invalid"
    r = await invitar_staff(
        email, ["view_bookings", "edit_prices"],
        sesion=_sesion_de(escenario, "owner", is_owner=True), pool=pool, cliente=cliente,
    )
    assert r.creado is True
    fila = await db_superuser.fetchrow(
        "select tenant_id, auth_user_id, is_owner, is_active from public.tenant_users where id = $1",
        r.tenant_user_id,
    )
    assert fila["tenant_id"] == escenario["tenant"]
    assert fila["auth_user_id"] == cliente.cuentas[email]
    assert fila["is_owner"] is False and fila["is_active"] is True
    claves = await db_superuser.fetch(
        "select permission_key from public.tenant_user_permissions where tenant_user_id = $1", r.tenant_user_id
    )
    assert {c["permission_key"] for c in claves} == {"view_bookings", "edit_prices"}
    auditoria = await db_superuser.fetchrow(
        "select actor_type, entity_id, metadata::text as metadata from public.audit_log "
        "where tenant_id = $1 and action = 'staff_invited'",
        escenario["tenant"],
    )
    assert auditoria["entity_id"] == r.tenant_user_id
    assert email not in (auditoria["metadata"] or "")


async def test_manage_staff_sin_ser_owner_no_puede_invitar(escenario, pool, db_superuser):
    cliente = _ClienteAuth(db_superuser, escenario)
    antes = await _contar(db_superuser, escenario)
    # El objeto de sesión dice owner, pero la base dice que no: manda la base.
    with pytest.raises(NoEsOwner):
        await invitar_staff(
            f"{uuid.uuid4().hex[:10]}@prueba.invalid", ["manage_staff"],
            sesion=_sesion_de(escenario, "gerente", is_owner=True), pool=pool, cliente=cliente,
        )
    assert cliente.llamadas == 0
    assert await _contar(db_superuser, escenario) == antes


async def test_si_la_api_falla_no_queda_tenant_user_y_el_reintento_no_duplica(escenario, pool, db_superuser):
    cliente = _ClienteAuth(db_superuser, escenario)
    cliente.fallos = 1
    email = f"{uuid.uuid4().hex[:10]}@prueba.invalid"
    sesion = _sesion_de(escenario, "owner", is_owner=True)
    antes = await _contar(db_superuser, escenario)

    with pytest.raises(ErrorInvitacion) as e:
        await invitar_staff(email, ["view_reports"], sesion=sesion, pool=pool, cliente=cliente)
    assert e.value.reintentable is True
    assert await _contar(db_superuser, escenario) == antes

    primera = await invitar_staff(email, ["view_reports"], sesion=sesion, pool=pool, cliente=cliente)
    segunda = await invitar_staff(email, ["view_reports"], sesion=sesion, pool=pool, cliente=cliente)
    assert primera.creado is True and segunda.creado is False
    assert segunda.tenant_user_id == primera.tenant_user_id
    assert await _contar(db_superuser, escenario) == antes + 1
    permisos = await db_superuser.fetchval(
        "select count(*) from public.tenant_user_permissions where tenant_user_id = $1", primera.tenant_user_id
    )
    assert permisos == 1


async def test_invitar_a_alguien_que_ya_es_staff_de_otro_tenant_no_lo_roba(escenario, pool, db_superuser):
    otro_tenant = uuid.uuid4()
    await db_superuser.execute(
        "insert into public.tenants (id, name, slug) values ($1, 'B', $2)", otro_tenant, f"it-perm-b-{uuid.uuid4().hex[:8]}"
    )
    email = f"{uuid.uuid4().hex[:10]}@prueba.invalid"
    cliente = _ClienteAuth(db_superuser, escenario)
    cuenta = uuid.uuid4()
    await db_superuser.execute("insert into auth.users (id, email) values ($1, $2)", cuenta, email)
    escenario["extra"].append(cuenta)
    cliente.cuentas[email] = cuenta
    await db_superuser.execute(
        "insert into public.tenant_users (tenant_id, auth_user_id) values ($1, $2)", otro_tenant, cuenta
    )
    try:
        with pytest.raises(StaffYaExiste):
            await invitar_staff(
                email, [], sesion=_sesion_de(escenario, "owner", is_owner=True), pool=pool, cliente=cliente
            )
        assert await db_superuser.fetchval(
            "select count(*) from public.tenant_users where auth_user_id = $1", cuenta
        ) == 1
    finally:
        await _borrar(db_superuser, "delete from public.tenant_users where tenant_id = $1", otro_tenant)
        await _borrar(db_superuser, "delete from public.tenants where id = $1", otro_tenant)
