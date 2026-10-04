"""dashboard-backend contra Postgres real (rol `dashboard_service`, RLS y estado vivo del tenant/staff)."""

import uuid
from contextlib import asynccontextmanager
from typing import Annotated

import pytest

pytestmark = pytest.mark.integracion

pytest.importorskip("asyncpg")

from fastapi import Depends  # noqa: E402

from dashboard_backend.dependencias import Transaccion  # noqa: E402
from dashboard_backend.tests.soporte import ORIGEN, app_de_prueba, http, token  # noqa: E402


class _PoolDeUnaConexion:
    """Adapta la conexión de `conectar_como("dashboard_service")` a la interfaz de pool (acquire).

    Sirve porque un request toma la conexión de a una vez (la autenticación termina antes de abrir la transacción
    del endpoint), así que nunca hay dos usos simultáneos.
    """

    def __init__(self, conn):
        self._conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self._conn


async def _crear_usuario_auth(conn, user_id):
    await conn.execute(
        "insert into auth.users (id, email) values ($1, $2)", user_id, f"{user_id.hex[:12]}@prueba.invalid"
    )


async def _borrar(conn, sql, *args):
    """DELETE de limpieza sin triggers: las invariantes de último owner las frenarían."""
    await conn.execute("set session_replication_role = replica")
    try:
        await conn.execute(sql, *args)
    finally:
        await conn.execute("set session_replication_role = origin")


@pytest.fixture
async def escenario(db_superuser):
    """Dos tenants activos; en el A: un owner, un staff con `view_bookings` y un staff sin permisos."""
    sufijo = uuid.uuid4().hex[:8]
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    for t, n in ((tenant_a, "A"), (tenant_b, "B")):
        await db_superuser.execute(
            "insert into public.tenants (id, name, slug) values ($1, $2, $3)", t, n, f"it-dash-{n.lower()}-{sufijo}"
        )
    owner, con_permiso, sin_permiso = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    ids = {}
    for nombre, u, es_owner in (("owner", owner, True), ("con", con_permiso, False), ("sin", sin_permiso, False)):
        await _crear_usuario_auth(db_superuser, u)
        ids[nombre] = await db_superuser.fetchval(
            "insert into public.tenant_users (tenant_id, auth_user_id, is_owner) values ($1, $2, $3) returning id",
            tenant_a,
            u,
            es_owner,
        )
    await db_superuser.execute(
        "insert into public.tenant_user_permissions (tenant_user_id, permission_key) values ($1, 'view_bookings')",
        ids["con"],
    )
    try:
        yield {"a": tenant_a, "b": tenant_b, "owner": owner, "con": con_permiso, "sin": sin_permiso}
    finally:
        await _borrar(db_superuser, "delete from public.tenant_user_permissions where tenant_id = $1", tenant_a)
        await _borrar(
            db_superuser, "delete from public.audit_log where tenant_id = any($1::uuid[])", [tenant_a, tenant_b]
        )
        await _borrar(db_superuser, "delete from public.tenant_users where tenant_id = $1", tenant_a)
        await _borrar(db_superuser, "delete from public.tenants where id = any($1::uuid[])", [tenant_a, tenant_b])
        await _borrar(
            db_superuser, "delete from auth.users where id = any($1::uuid[])", [owner, con_permiso, sin_permiso]
        )


@pytest.fixture
async def app(conectar_como):
    conn = await conectar_como("dashboard_service")
    app, _, _ = app_de_prueba(pool=_PoolDeUnaConexion(conn))
    deps = app.state.dependencias

    @app.get("/prueba/reservas")
    async def reservas(tx: Annotated[Transaccion, Depends(deps.tx_con_permiso("view_bookings"))]):
        return {"ok": True}

    @app.post("/prueba/contexto")
    async def contexto(tx: Annotated[Transaccion, Depends(deps.tx)]):
        return {
            "tenant": await tx.conn.fetchval("select current_setting('app.current_tenant_id', true)"),
            "actor": await tx.conn.fetchval("select current_setting('app.current_actor_id', true)"),
            "tenants_visibles": [str(f["id"]) for f in await tx.conn.fetch("select id from public.tenants")],
        }

    return app


def _cookie(**kw):
    return {"Cookie": f"dash_acceso={token(**kw)}"}


async def test_health_200_con_la_base_real(app):
    r = await http(app).get("/health")
    assert r.status_code == 200


async def test_el_contexto_de_tenant_llega_a_la_base_y_la_rls_solo_deja_ver_el_propio(app, escenario):
    # El body trae el tenant B: se ignora; manda el del JWT (A).
    r = await http(app).post(
        "/prueba/contexto",
        json={"tenant_id": str(escenario["b"])},
        headers={
            "Origin": ORIGEN,
            **_cookie(user_id=escenario["owner"], tenant_id=escenario["a"], is_owner=True, aal="aal2"),
        },
    )
    assert r.status_code == 200
    assert r.json() == {
        "tenant": str(escenario["a"]),
        "actor": str(escenario["owner"]),
        "tenants_visibles": [str(escenario["a"])],
    }


async def test_endpoint_protegido_de_un_tenant_suspended_es_403(app, escenario, db_superuser):
    h = _cookie(user_id=escenario["con"], tenant_id=escenario["a"])
    assert (await http(app).get("/prueba/reservas", headers=h)).status_code == 200
    await db_superuser.execute("update public.tenants set status = 'suspended' where id = $1", escenario["a"])
    r = await http(app).get("/prueba/reservas", headers=h)  # mismo token
    assert r.status_code == 403
    assert r.json()["detail"]["codigo"] == "tenant_no_activo"


async def test_sin_el_permiso_es_403_con_la_base_real(app, escenario):
    r = await http(app).get("/prueba/reservas", headers=_cookie(user_id=escenario["sin"], tenant_id=escenario["a"]))
    assert r.status_code == 403
    assert r.json()["detail"]["codigo"] == "permiso_denegado"


async def test_el_owner_pasa_sin_filas_de_permisos(app, escenario):
    h = _cookie(user_id=escenario["owner"], tenant_id=escenario["a"], is_owner=True, aal="aal2")
    assert (await http(app).get("/prueba/reservas", headers=h)).status_code == 200


async def test_revocar_el_permiso_corta_el_acceso_en_el_siguiente_request(app, escenario, db_superuser):
    h = _cookie(user_id=escenario["con"], tenant_id=escenario["a"])
    assert (await http(app).get("/prueba/reservas", headers=h)).status_code == 200
    await db_superuser.execute(
        "delete from public.tenant_user_permissions where tenant_user_id = "
        "(select id from public.tenant_users where auth_user_id = $1)",
        escenario["con"],
    )
    assert (await http(app).get("/prueba/reservas", headers=h)).status_code == 403
