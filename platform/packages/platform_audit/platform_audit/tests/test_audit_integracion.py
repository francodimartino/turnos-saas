"""platform_audit contra Postgres real: el actor lo deriva la base del rol de la conexión."""

import json
import uuid

import pytest

from platform_audit import MetadataConPII, registrar

pytestmark = pytest.mark.integracion

asyncpg = pytest.importorskip("asyncpg")


@pytest.fixture
async def tenant(db_superuser):
    tenant_id = uuid.uuid4()
    await db_superuser.execute(
        "insert into public.tenants (id, name, slug) values ($1, 'A', $2)", tenant_id, f"it-aud-{uuid.uuid4().hex[:8]}"
    )
    try:
        yield tenant_id
    finally:
        async with db_superuser.transaction():
            # audit_log es solo-agregar: el trigger se apaga solo en la limpieza, dentro de la transacción
            await db_superuser.execute("alter table public.audit_log disable trigger audit_log_solo_agregar_row")
            await db_superuser.execute("delete from public.audit_log where tenant_id = $1", tenant_id)
            await db_superuser.execute("alter table public.audit_log enable trigger audit_log_solo_agregar_row")
            await db_superuser.execute("delete from public.tenants where id = $1", tenant_id)


async def _tx(conn, tenant_id, actor_id):
    tx = conn.transaction()
    await tx.start()
    await conn.execute("select set_config('app.current_tenant_id', $1, true)", str(tenant_id))
    await conn.execute("select set_config('app.current_actor_id', $1, true)", str(actor_id))
    return tx


@pytest.mark.parametrize(("rol", "actor_type"), [("dashboard_service", "staff"), ("bot_service", "bot")])
async def test_registrar_escribe_la_fila_con_el_actor_type_del_rol(conectar_como, db_superuser, tenant, rol, actor_type):
    actor = uuid.uuid4()
    entidad = uuid.uuid4()
    accion = f"it_platform_audit_{rol}"
    conn = await conectar_como(rol)
    tx = await _tx(conn, tenant, actor)
    try:
        id_ = await registrar(conn, accion, "bookings", entidad, {"motivo": "prueba"})
    finally:
        await tx.commit()

    fila = await db_superuser.fetchrow("select * from public.audit_log where id = $1", id_)
    assert fila["action"] == accion
    assert fila["actor_type"] == actor_type
    assert fila["tenant_id"] == tenant
    assert fila["actor_id"] == actor
    assert fila["entity"] == "bookings"
    assert fila["entity_id"] == entidad
    assert json.loads(fila["metadata"]) == {"motivo": "prueba"}


async def test_registrar_con_pii_no_escribe_nada(conectar_como, db_superuser, tenant):
    conn = await conectar_como("dashboard_service")
    tx = await _tx(conn, tenant, uuid.uuid4())
    try:
        with pytest.raises(MetadataConPII):
            await registrar(conn, "it_platform_audit_pii", metadata={"phone": "+5491155550000"})
    finally:
        await tx.commit()
    assert await db_superuser.fetchval("select count(*) from public.audit_log where action = 'it_platform_audit_pii'") == 0


async def test_registrar_en_otro_tenant_lo_rechaza_la_base(conectar_como, tenant):
    conn = await conectar_como("dashboard_service")
    tx = await _tx(conn, tenant, uuid.uuid4())
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await registrar(conn, "it_platform_audit_cruzado", tenant_id=uuid.uuid4())
    finally:
        await tx.rollback()
