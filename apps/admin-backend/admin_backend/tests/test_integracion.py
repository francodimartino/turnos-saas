"""admin-backend contra Postgres real, conectado como el rol `platform_admin` (gate, ciclo de vida y settings)."""

import json
import uuid
from contextlib import asynccontextmanager

import pytest

pytestmark = pytest.mark.integracion

NOMBRE = "Lucía Gómez"

pytest.importorskip("asyncpg")

from admin_backend.tests.soporte import AuthAdminFalso, app_de_prueba, bearer, http  # noqa: E402


class _PoolDeUnaConexion:
    """Adapta la conexión de `conectar_como("platform_admin")` a la interfaz de pool (acquire).

    Un request toma la conexión de a una vez (la autenticación termina antes de abrir la transacción de la
    operación), así que nunca hay dos usos simultáneos.
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


@pytest.fixture
async def escenario(db_superuser):
    """Un platform admin real, un staff de tenant (owner) y un tenant donde trabaja ese staff."""
    sufijo = uuid.uuid4().hex[:8]
    admin, staff, falso_admin = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    tenant = uuid.uuid4()
    for u in (admin, staff, falso_admin):
        await _crear_usuario_auth(db_superuser, u)
    await db_superuser.execute("insert into public.platform_admins (auth_user_id) values ($1)", admin)
    await db_superuser.execute(
        "insert into public.tenants (id, name, slug) values ($1, 'Base', $2)", tenant, f"it-admbk-{sufijo}"
    )
    await db_superuser.execute(
        "insert into public.tenant_users (tenant_id, auth_user_id, is_owner) values ($1, $2, true)", tenant, staff
    )
    creados: list[uuid.UUID] = []
    estado = {"admin": admin, "staff": staff, "falso_admin": falso_admin, "tenant": tenant, "creados": creados}
    try:
        yield estado
    finally:
        todos = [tenant, *creados]
        async with db_superuser.transaction():
            # Limpieza sin triggers (último owner/admin, audit_log solo-agregar): vale solo dentro de esta transacción
            await db_superuser.execute("set local session_replication_role = replica")
            await db_superuser.execute("delete from public.audit_log where tenant_id = any($1::uuid[])", todos)
            await db_superuser.execute("delete from public.audit_log where actor_id = $1", admin)
            await db_superuser.execute("delete from public.tenant_users where tenant_id = any($1::uuid[])", todos)
            await db_superuser.execute("delete from public.tenants where id = any($1::uuid[])", todos)
            await db_superuser.execute("delete from public.platform_admins where auth_user_id = $1", admin)
            await db_superuser.execute(
                "delete from auth.users where id = any($1::uuid[])",
                [admin, staff, falso_admin, *estado.get("auth_creados", [])],
            )


@pytest.fixture
async def app(conectar_como):
    conn = await conectar_como("platform_admin")
    app, _, _ = app_de_prueba(pool=_PoolDeUnaConexion(conn))
    return app


def _rutas_del_panel(tenant_id):
    """Todos los endpoints de negocio del panel: (método, ruta, cuerpo)."""
    t = f"/tenants/{tenant_id}"
    return [
        ("GET", "/tenants", None),
        ("POST", "/tenants", {"name": "X", "slug": "x-gate", "owner_email": "x@prueba.invalid", "owner_nombre": "X Y"}),
        ("GET", t, None),
        ("POST", f"{t}/suspender", None),
        ("POST", f"{t}/levantar-suspension", None),
        ("POST", f"{t}/dar-de-baja", None),
        ("POST", f"{t}/reactivar", None),
        ("POST", f"{t}/reenviar-invitacion", None),
        ("GET", f"{t}/facturacion", None),
        ("PUT", f"{t}/facturacion", {"billing_notes": "x"}),
        ("GET", "/settings", None),
        ("PUT", "/settings/tenant_deletion_grace_period_days", {"valor": 10}),
    ]


async def test_staff_de_tenant_sin_is_platform_admin_recibe_403_en_todos_los_endpoints(app, escenario):
    cabeceras = bearer(
        user_id=escenario["staff"], tenant_id=escenario["tenant"], is_owner=True, is_platform_admin=False
    )
    async with http(app) as c:
        for metodo, ruta, cuerpo in _rutas_del_panel(escenario["tenant"]):
            r = await c.request(metodo, ruta, json=cuerpo, headers=cabeceras)
            assert r.status_code == 403, (metodo, ruta, r.text)
            assert r.json()["detail"]["codigo"] == "no_es_platform_admin"
        # y no tocó nada: el tenant sigue activo
        assert await _estado(c, escenario) == "active"


async def test_platform_admin_con_aal1_recibe_403_en_todos_los_endpoints(app, escenario):
    cabeceras = bearer(user_id=escenario["admin"], aal="aal1")
    async with http(app) as c:
        for metodo, ruta, cuerpo in _rutas_del_panel(escenario["tenant"]):
            r = await c.request(metodo, ruta, json=cuerpo, headers=cabeceras)
            assert r.status_code == 403, (metodo, ruta, r.text)
            assert r.json()["detail"]["codigo"] == "mfa_requerida"


async def test_claim_de_platform_admin_sin_fila_en_platform_admins_recibe_403(app, escenario):
    """El claim solo habilita: la confirmación en vivo contra `platform_admins` es la que manda."""
    cabeceras = bearer(user_id=escenario["falso_admin"], is_platform_admin=True)
    async with http(app) as c:
        for metodo, ruta, cuerpo in _rutas_del_panel(escenario["tenant"]):
            r = await c.request(metodo, ruta, json=cuerpo, headers=cabeceras)
            assert r.status_code == 403, (metodo, ruta, r.text)


async def _estado(c, escenario):
    r = await c.get(f"/tenants/{escenario['tenant']}", headers=bearer(user_id=escenario["admin"]))
    assert r.status_code == 200, r.text
    return r.json()["status"]


async def test_ciclo_de_vida_completo_y_transiciones_invalidas_409(app, escenario):
    t = f"/tenants/{escenario['tenant']}"
    h = bearer(user_id=escenario["admin"])
    async with http(app) as c:
        assert (await c.post(f"{t}/levantar-suspension", headers=h)).status_code == 409  # active no se levanta
        assert (await c.post(f"{t}/reactivar", headers=h)).status_code == 409  # active no se reactiva
        assert (await c.post(f"{t}/suspender", headers=h)).status_code == 204
        assert await _estado(c, escenario) == "suspended"
        assert (await c.post(f"{t}/suspender", headers=h)).status_code == 409
        assert (await c.post(f"{t}/levantar-suspension", headers=h)).status_code == 204
        assert (await c.post(f"{t}/dar-de-baja", headers=h)).status_code == 204
        assert await _estado(c, escenario) == "inactive"
        assert (await c.post(f"{t}/suspender", headers=h)).status_code == 409
        assert (await c.post(f"{t}/reactivar", headers=h)).status_code == 204
        assert await _estado(c, escenario) == "active"
        assert (await c.post(f"/tenants/{uuid.uuid4()}/suspender", headers=h)).status_code == 404


async def test_alta_con_invitacion_caida_queda_pendiente_y_se_reenvia(conectar_como, db_superuser, escenario):
    conn = await conectar_como("platform_admin")
    caida = AuthAdminFalso(falla=True)
    app, _, _ = app_de_prueba(pool=_PoolDeUnaConexion(conn), auth_admin=caida)
    h = bearer(user_id=escenario["admin"])
    slug = f"it-admbk-alta-{uuid.uuid4().hex[:8]}"
    email = f"duenia-{uuid.uuid4().hex[:8]}@prueba.invalid"
    async with http(app) as c:
        r = await c.post(
            "/tenants", json={"name": "Alta", "slug": slug, "owner_email": email, "owner_nombre": NOMBRE}, headers=h
        )
        assert r.status_code == 201, r.text
        assert r.json()["estado_invitacion"] == "invite_pendiente"
        nuevo = uuid.UUID(r.json()["tenant_id"])
        escenario["creados"].append(nuevo)
        assert email not in r.text  # el email no vuelve en ninguna respuesta
        ficha = await c.get(f"/tenants/{nuevo}", headers=h)
        assert ficha.json()["invitacion_pendiente"] is True
        # decisión de producto: el email se ve siempre; el nombre queda null hasta que haya cuenta
        assert (ficha.json()["owner_email"], ficha.json()["owner_nombre"]) == (email, None)
        repetido = await c.post(
            "/tenants", json={"name": "Otra", "slug": slug, "owner_email": email, "owner_nombre": NOMBRE}, headers=h
        )
        assert repetido.status_code == 409
        invalido = await c.post(
            "/tenants",
            json={"name": "Otra", "slug": "BAD slug", "owner_email": email, "owner_nombre": NOMBRE},
            headers=h,
        )
        assert invalido.status_code == 422

    # el reenvío usa el email guardado; con el Auth Admin API ya sano, el owner queda vinculado
    sana = _AuthQueCreaCuenta(db_superuser, escenario)
    app2, _, _ = app_de_prueba(pool=_PoolDeUnaConexion(conn), auth_admin=sana)
    async with http(app2) as c:
        r = await c.post(f"/tenants/{nuevo}/reenviar-invitacion", json={"owner_nombre": NOMBRE}, headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["estado_invitacion"] == "ok"
        assert sana.invitados == [email]
        assert sana.nombres == [NOMBRE]
        otra_vez = await c.post(f"/tenants/{nuevo}/reenviar-invitacion", headers=h)
        assert otra_vez.status_code == 409


class _AuthQueCreaCuenta(AuthAdminFalso):
    """Como el invite real: crea la cuenta en auth.users (la limpia el fixture `escenario`)."""

    def __init__(self, db, escenario):
        super().__init__()
        self._db = db
        self._escenario = escenario
        self.emails: dict[uuid.UUID, str] = {}

    async def invitar(self, email, nombre=None):
        user_id = uuid.uuid4()
        self.emails[user_id] = f"{user_id.hex[:8]}.{email}"
        await self._db.execute(
            "insert into auth.users (id, email, raw_user_meta_data) values ($1, $2, $3::jsonb)",
            user_id,
            self.emails[user_id],
            json.dumps({"full_name": nombre} if nombre else {}),
        )
        self.invitados.append(email)
        self.nombres.append(nombre)
        self._escenario.setdefault("auth_creados", []).append(user_id)
        return user_id


async def test_ficha_y_lista_muestran_nombre_y_email_del_owner(conectar_como, db_superuser, escenario):
    conn = await conectar_como("platform_admin")
    sana = _AuthQueCreaCuenta(db_superuser, escenario)
    app, _, _ = app_de_prueba(pool=_PoolDeUnaConexion(conn), auth_admin=sana)
    h = bearer(user_id=escenario["admin"])
    slug = f"it-admbk-id-{uuid.uuid4().hex[:8]}"
    email = f"duenia-{uuid.uuid4().hex[:8]}@prueba.invalid"
    async with http(app) as c:
        r = await c.post(
            "/tenants", json={"name": "Id", "slug": slug, "owner_email": email, "owner_nombre": NOMBRE}, headers=h
        )
        assert r.status_code == 201, r.text
        nuevo = r.json()["tenant_id"]
        escenario["creados"].append(uuid.UUID(nuevo))
        assert NOMBRE not in r.text and email not in r.text
        esperado_email = sana.emails[escenario["auth_creados"][-1]]
        ficha = (await c.get(f"/tenants/{nuevo}", headers=h)).json()
        assert (ficha["owner_nombre"], ficha["owner_email"]) == (NOMBRE, esperado_email)
        lista = {t["id"]: t for t in (await c.get("/tenants", headers=h)).json()}
        assert (lista[nuevo]["owner_nombre"], lista[nuevo]["owner_email"]) == (NOMBRE, esperado_email)
        # el tenant del escenario tiene un owner con cuenta sin nombre: nombre null, email visible
        base = lista[str(escenario["tenant"])]
        assert base["owner_nombre"] is None and base["owner_email"] == f"{escenario['staff'].hex[:12]}@prueba.invalid"
        # sin nombre válido no se crea nada
        sin_nombre = await c.post("/tenants", json={"name": "Id", "slug": slug + "-x", "owner_email": email}, headers=h)
        assert sin_nombre.status_code == 422 and "owner_nombre" in sin_nombre.json()["detail"]["mensaje"]
    assert await db_superuser.fetchval("select count(*) from public.tenants where slug = $1", slug + "-x") == 0
    filas = await db_superuser.fetch("select * from public.audit_log where tenant_id = $1", uuid.UUID(nuevo))
    assert NOMBRE not in repr([dict(f) for f in filas])


async def test_settings_get_put_y_facturacion(app, escenario, db_superuser):
    h = bearer(user_id=escenario["admin"])
    t = f"/tenants/{escenario['tenant']}"
    clave = "tenant_deletion_grace_period_days"
    previo = await db_superuser.fetchval("select value::text from public.platform_settings where key = $1", clave)
    try:
        async with http(app) as c:
            r = await c.put(f"/settings/{clave}", json={"valor": 45}, headers=h)
            assert r.status_code == 200 and r.json()["hasta"] == 45
            assert (await c.get("/settings", headers=h)).json()[clave] == 45
            assert (await c.put(f"/settings/{clave}", json={"valor": 0}, headers=h)).status_code == 422
            assert (await c.put("/settings/clave_inventada", json={"valor": 1}, headers=h)).status_code == 422

            r = await c.put(
                f"{t}/facturacion", json={"paid_until": "2026-12-31", "billing_notes": "transf."}, headers=h
            )
            assert r.status_code == 200, r.text
            r = await c.get(f"{t}/facturacion", headers=h)
            assert r.json() == {
                "tenant_id": str(escenario["tenant"]),
                "paid_until": "2026-12-31",
                "billing_notes": "transf.",
            }
    finally:
        if previo is None:
            await db_superuser.execute("delete from public.platform_settings where key = $1", clave)
        else:
            await db_superuser.execute(
                "update public.platform_settings set value = $2::jsonb where key = $1", clave, previo
            )
