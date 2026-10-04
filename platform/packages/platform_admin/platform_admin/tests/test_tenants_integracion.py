"""platform_admin contra Postgres real, conectado como el rol `platform_admin` (grants, policies, trigger y audit)."""

import uuid
from contextlib import asynccontextmanager

import pytest

from platform_admin import (
    ErrorInvitacion,
    InvitacionNoPendiente,
    SlugEnUso,
    TenantNoEncontrado,
    TransicionInvalida,
    create_tenant,
    dar_de_baja,
    levantar_suspension,
    listar_tenants,
    obtener_tenant,
    reactivar,
    reenviar_invitacion,
    suspender,
)

pytestmark = pytest.mark.integracion

asyncpg = pytest.importorskip("asyncpg")

ACTOR = uuid.uuid4()


class PoolDeUnaConexion:
    """Adapta una conexión de `conectar_como` a la interfaz de pool que usa `platform_tenancy`."""

    def __init__(self, conn):
        self._conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self._conn


class AuthFalso:
    """Auth Admin API falsa: 'crea' el usuario en auth.users (como lo haría el invite real) o falla."""

    def __init__(self, db_superuser, falla=False):
        self._db = db_superuser
        self.falla = falla
        self.creados: list[uuid.UUID] = []

    async def invitar(self, email):
        if self.falla:
            raise ErrorInvitacion("red caída")
        user_id = uuid.uuid4()
        await self._db.execute("insert into auth.users (id, email) values ($1, $2)", user_id, email)
        self.creados.append(user_id)
        return user_id


@pytest.fixture
async def entorno(conectar_como, db_superuser):
    """Pool como `platform_admin`; registra los tenants creados y limpia todo al terminar."""
    pool = PoolDeUnaConexion(await conectar_como("platform_admin"))
    tenants: list[uuid.UUID] = []
    auth = AuthFalso(db_superuser)
    try:
        yield pool, tenants, auth
    finally:
        async with db_superuser.transaction():
            # audit_log es solo-agregar: el trigger se apaga únicamente en la limpieza, dentro de la transacción
            await db_superuser.execute("alter table public.audit_log disable trigger audit_log_solo_agregar_row")
            await db_superuser.execute("delete from public.audit_log where tenant_id = any($1::uuid[])", tenants)
            await db_superuser.execute("alter table public.audit_log enable trigger audit_log_solo_agregar_row")
            await db_superuser.execute("delete from public.tenant_users where tenant_id = any($1::uuid[])", tenants)
            await db_superuser.execute("delete from public.tenants where id = any($1::uuid[])", tenants)
            await db_superuser.execute("delete from auth.users where id = any($1::uuid[])", auth.creados)


def _datos():
    return {"name": "Estética de prueba", "slug": f"it-adm-{uuid.uuid4().hex[:10]}"}


async def _crear(entorno, auth=None):
    pool, tenants, auth_por_defecto = entorno
    res = await create_tenant(pool, auth or auth_por_defecto, _datos(), "duenia@prueba.invalid", actor_id=ACTOR)
    tenants.append(res.tenant_id)
    return res


async def _auditoria(db_superuser, tenant_id):
    filas = await db_superuser.fetch(
        "select action, actor_type, actor_id, entity, entity_id from public.audit_log "
        "where tenant_id = $1 order by created_at, id",
        tenant_id,
    )
    return [dict(f) for f in filas]


async def test_create_tenant_ok_crea_tenant_owner_cuenta_vinculada_y_audita(entorno, db_superuser):
    res = await _crear(entorno)
    assert res.estado == "ok"

    tenant = await db_superuser.fetchrow("select * from public.tenants where id = $1", res.tenant_id)
    assert tenant["status"] == "active" and tenant["deactivated_at"] is None
    owner = await db_superuser.fetchrow("select * from public.tenant_users where id = $1", res.owner_id)
    assert owner["tenant_id"] == res.tenant_id and owner["is_owner"] is True and owner["is_active"] is True
    assert owner["auth_user_id"] == entorno[2].creados[0]

    auditoria = await _auditoria(db_superuser, res.tenant_id)
    assert [a["action"] for a in auditoria] == ["tenant_create", "tenant_invite_sent"]
    assert all(a["actor_type"] == "platform_admin" and a["actor_id"] == ACTOR for a in auditoria)


async def test_create_tenant_invite_falla_deja_tenant_y_owner_pendientes(entorno, db_superuser):
    res = await _crear(entorno, AuthFalso(db_superuser, falla=True))
    assert res.estado == "invite_pendiente"

    assert await db_superuser.fetchval("select status from public.tenants where id = $1", res.tenant_id) == "active"
    owner = await db_superuser.fetchrow("select * from public.tenant_users where id = $1", res.owner_id)
    assert owner["is_owner"] is True and owner["auth_user_id"] is None
    assert (await obtener_tenant(entorno[0], res.tenant_id)).invitacion_pendiente is True
    acciones = [a["action"] for a in await _auditoria(db_superuser, res.tenant_id)]
    assert acciones == ["tenant_create", "tenant_invite_failed"]


async def test_reenviar_invitacion_completa_el_alta_y_despues_ya_no_hay_pendiente(entorno, db_superuser):
    pool, _, auth = entorno
    res = await _crear(entorno, AuthFalso(db_superuser, falla=True))

    out = await reenviar_invitacion(pool, auth, res.tenant_id, "duenia@prueba.invalid", actor_id=ACTOR)
    assert out.estado == "ok"
    owner = await db_superuser.fetchrow("select auth_user_id from public.tenant_users where id = $1", res.owner_id)
    assert owner["auth_user_id"] == auth.creados[0]
    assert (await obtener_tenant(pool, res.tenant_id)).invitacion_pendiente is False
    acciones = [a["action"] for a in await _auditoria(db_superuser, res.tenant_id)]
    assert acciones == ["tenant_create", "tenant_invite_failed", "tenant_invite_sent"]

    with pytest.raises(InvitacionNoPendiente):
        await reenviar_invitacion(pool, auth, res.tenant_id, "duenia@prueba.invalid", actor_id=ACTOR)


async def test_reenviar_invitacion_tenant_inexistente(entorno):
    with pytest.raises(TenantNoEncontrado):
        await reenviar_invitacion(entorno[0], entorno[2], uuid.uuid4(), "x@prueba.invalid", actor_id=ACTOR)


async def test_create_tenant_slug_repetido_es_error_de_dominio_y_no_deja_basura(entorno, db_superuser):
    pool, tenants, auth = entorno
    datos = _datos()
    res = await create_tenant(pool, auth, datos, "a@prueba.invalid", actor_id=ACTOR)
    tenants.append(res.tenant_id)
    invitados_antes = len(auth.creados)
    with pytest.raises(SlugEnUso):
        await create_tenant(pool, auth, datos, "b@prueba.invalid", actor_id=ACTOR)
    assert len(auth.creados) == invitados_antes  # no se invitó a nadie
    assert await db_superuser.fetchval("select count(*) from public.tenants where slug = $1", datos["slug"]) == 1


async def test_ciclo_de_vida_completo_con_deactivated_at_y_audit(entorno, db_superuser):
    pool = entorno[0]
    t = (await _crear(entorno)).tenant_id

    async def _fila():
        return await db_superuser.fetchrow("select status, deactivated_at from public.tenants where id = $1", t)

    await suspender(pool, t, actor_id=ACTOR)
    fila = await _fila()
    assert fila["status"] == "suspended" and fila["deactivated_at"] is None

    await levantar_suspension(pool, t, actor_id=ACTOR)
    assert (await _fila())["status"] == "active"

    await dar_de_baja(pool, t, actor_id=ACTOR)
    fila = await _fila()
    assert fila["status"] == "inactive" and fila["deactivated_at"] is not None

    await reactivar(pool, t, actor_id=ACTOR)
    fila = await _fila()
    assert fila["status"] == "active" and fila["deactivated_at"] is None

    await suspender(pool, t, actor_id=ACTOR)
    await dar_de_baja(pool, t, actor_id=ACTOR)  # suspended -> inactive también es válido
    assert (await _fila())["status"] == "inactive"

    auditoria = await _auditoria(db_superuser, t)
    acciones = [a["action"] for a in auditoria]
    assert acciones == [
        "tenant_create",
        "tenant_invite_sent",
        "tenant_suspend",
        "tenant_unsuspend",
        "tenant_deactivate",
        "tenant_reactivate",
        "tenant_suspend",
        "tenant_deactivate",
    ]
    assert all(a["actor_type"] == "platform_admin" for a in auditoria)
    assert all(a["actor_id"] == ACTOR for a in auditoria)
    assert all(a["entity_id"] == t for a in auditoria if a["entity"] == "tenants")


async def test_transicion_invalida_devuelve_error_de_dominio_y_no_cambia_nada(entorno, db_superuser):
    pool = entorno[0]
    t = (await _crear(entorno)).tenant_id
    for accion in (levantar_suspension, reactivar):  # un tenant activo no se "levanta" ni se "reactiva"
        with pytest.raises(TransicionInvalida):
            await accion(pool, t, actor_id=ACTOR)
    await suspender(pool, t, actor_id=ACTOR)
    with pytest.raises(TransicionInvalida):
        await suspender(pool, t, actor_id=ACTOR)
    assert await db_superuser.fetchval("select status from public.tenants where id = $1", t) == "suspended"
    # los intentos fallidos no dejan auditoría de transición
    acciones = [a["action"] for a in await _auditoria(db_superuser, t)]
    assert acciones.count("tenant_suspend") == 1 and "tenant_reactivate" not in acciones


async def test_reactivar_un_purged_se_rechaza(entorno, db_superuser):
    pool = entorno[0]
    t = (await _crear(entorno)).tenant_id
    await dar_de_baja(pool, t, actor_id=ACTOR)
    # la purga la hace el job (purge_job); en el test se simula con el superusuario, respetando el trigger
    await db_superuser.execute("update public.tenants set status = 'purged' where id = $1", t)
    for accion in (reactivar, suspender, dar_de_baja, levantar_suspension):
        with pytest.raises(TransicionInvalida):
            await accion(pool, t, actor_id=ACTOR)
    assert await db_superuser.fetchval("select status from public.tenants where id = $1", t) == "purged"


async def test_el_trigger_de_la_base_rechaza_la_transicion_aunque_se_salte_la_validacion_de_python(entorno):
    """Defensa en profundidad: un UPDATE directo como platform_admin sobre un estado inválido falla en la base."""
    pool = entorno[0]
    t = (await _crear(entorno)).tenant_id
    conn = pool._conn
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute("update public.tenants set status = 'purged' where id = $1", t)


async def test_ciclo_de_vida_de_un_tenant_inexistente(entorno):
    with pytest.raises(TenantNoEncontrado):
        await suspender(entorno[0], uuid.uuid4(), actor_id=ACTOR)


async def test_sin_contexto_de_tenant_platform_admin_lista_y_ve_todos_los_tenants(entorno):
    pool = entorno[0]
    a = (await _crear(entorno)).tenant_id
    b = (await _crear(entorno)).tenant_id
    await suspender(pool, b, actor_id=ACTOR)

    todos = {t.id: t for t in await listar_tenants(pool, actor_id=ACTOR)}
    assert a in todos and b in todos
    assert todos[a].status == "active" and todos[b].status == "suspended"
    assert {t.id for t in await listar_tenants(pool, status="suspended")} >= {b}
    assert a not in {t.id for t in await listar_tenants(pool, status="suspended")}
    assert (await obtener_tenant(pool, a)).id == a
    assert (await obtener_tenant(pool, b)).slug == todos[b].slug
