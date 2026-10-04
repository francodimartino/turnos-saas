"""platform_secrets contra Postgres real (Supabase Vault): aislamiento entre tenants y permisos por rol."""

import json
import logging
import uuid

import pytest

from platform_secrets import AccesoSecretoDenegado, Secreto, crear_almacen

pytestmark = pytest.mark.integracion

asyncpg = pytest.importorskip("asyncpg")

VALOR = json.dumps({"bot_token": "tok-it-secrets-789"})


@pytest.fixture
async def dos_tenants(db_superuser):
    sufijo = uuid.uuid4().hex[:8]
    a, b = uuid.uuid4(), uuid.uuid4()
    await db_superuser.execute(
        "insert into public.tenants (id, name, slug) values ($1, 'A', $2), ($3, 'B', $4)",
        a, f"it-sec-a-{sufijo}", b, f"it-sec-b-{sufijo}",
    )
    try:
        yield a, b
    finally:
        tenants = [a, b]
        async with db_superuser.transaction():
            await db_superuser.execute("alter table public.audit_log disable trigger audit_log_solo_agregar_row")
            await db_superuser.execute("delete from public.audit_log where tenant_id = any($1::uuid[])", tenants)
            await db_superuser.execute("alter table public.audit_log enable trigger audit_log_solo_agregar_row")
            secretos = await db_superuser.fetch(
                "select secret_id from public.tenant_integrations where tenant_id = any($1::uuid[]) "
                "and secret_id is not null",
                tenants,
            )
            await db_superuser.execute(
                "delete from public.tenant_integrations where tenant_id = any($1::uuid[])", tenants
            )
            await db_superuser.execute(
                "delete from vault.secrets where id = any($1::uuid[])", [f["secret_id"] for f in secretos]
            )
            await db_superuser.execute("delete from public.tenants where id = any($1::uuid[])", tenants)


async def _tx(conn, tenant_id):
    tx = conn.transaction()
    await tx.start()
    await conn.execute("select set_config('app.current_tenant_id', $1, true)", str(tenant_id))
    return tx


async def _guardar_como_admin(conectar_como, tenant_id, tipo="telegram_bot", valor=VALOR):
    admin = await conectar_como("platform_admin")
    return await crear_almacen(admin).guardar(tenant_id, tipo, {"webhook_key": f"it-{uuid.uuid4()}"}, valor)


async def test_guardar_como_platform_admin_y_obtener_como_el_tenant(conectar_como, dos_tenants, caplog):
    tenant_a, _ = dos_tenants
    integracion = await _guardar_como_admin(conectar_como, tenant_a)

    conn = await conectar_como("bot_service")
    tx = await _tx(conn, tenant_a)
    try:
        almacen = crear_almacen(conn)
        assert await almacen.buscar("telegram_bot") == integracion
        with caplog.at_level(logging.DEBUG):
            secreto = await almacen.obtener(integracion)
    finally:
        await tx.rollback()

    assert isinstance(secreto, Secreto)
    assert secreto.revelar() == VALOR
    assert "tok-it-secrets-789" not in repr(secreto)
    assert "tok-it-secrets-789" not in str(secreto)
    assert "tok-it-secrets-789" not in caplog.text


async def test_obtener_el_secret_de_otro_tenant_falla(conectar_como, dos_tenants):
    tenant_a, tenant_b = dos_tenants
    integracion_de_a = await _guardar_como_admin(conectar_como, tenant_a)

    conn = await conectar_como("dashboard_service")
    tx = await _tx(conn, tenant_b)
    try:
        with pytest.raises(AccesoSecretoDenegado) as exc:
            await crear_almacen(conn).obtener(integracion_de_a)
    finally:
        await tx.rollback()
    assert "tok-it-secrets-789" not in str(exc.value)


async def test_obtener_una_integracion_inexistente_da_el_mismo_error_que_una_ajena(conectar_como, dos_tenants):
    tenant_a, _ = dos_tenants
    conn = await conectar_como("dashboard_service")
    tx = await _tx(conn, tenant_a)
    try:
        with pytest.raises(AccesoSecretoDenegado):
            await crear_almacen(conn).obtener(uuid.uuid4())
    finally:
        await tx.rollback()


@pytest.mark.parametrize(
    "rol", ["bot_service", "dashboard_service", "platform_admin_readonly", "purge_job", "scheduler_job"]
)
async def test_guardar_con_un_rol_distinto_de_platform_admin_falla(conectar_como, db_superuser, dos_tenants, rol):
    tenant_a, _ = dos_tenants
    conn = await conectar_como(rol)
    with pytest.raises(AccesoSecretoDenegado) as exc:
        await crear_almacen(conn).guardar(tenant_a, "telegram_bot", {"webhook_key": f"it-{uuid.uuid4()}"}, VALOR)
    assert "tok-it-secrets-789" not in str(exc.value)
    filas = await db_superuser.fetchval(
        "select count(*) from public.tenant_integrations where tenant_id = $1", tenant_a
    )
    assert filas == 0


async def test_actualizar_el_secret_reemplaza_el_valor(conectar_como, dos_tenants):
    tenant_a, _ = dos_tenants
    primero = await _guardar_como_admin(conectar_como, tenant_a, valor="viejo-valor")
    admin = await conectar_como("platform_admin")
    config = {"webhook_key": f"it-{uuid.uuid4()}"}
    segundo = await crear_almacen(admin).guardar(tenant_a, "telegram_bot", config, "nuevo-valor")
    assert segundo == primero

    conn = await conectar_como("bot_service")
    tx = await _tx(conn, tenant_a)
    try:
        assert (await crear_almacen(conn).obtener(primero)).revelar() == "nuevo-valor"
    finally:
        await tx.rollback()


async def test_google_calendar_del_profesional_se_guarda_obtiene_y_elimina(conectar_como, db_superuser, dos_tenants):
    tenant_a, _ = dos_tenants
    profesional = uuid.uuid4()
    await db_superuser.execute(
        "insert into public.professionals (id, tenant_id, name) values ($1, $2, 'Prof')", profesional, tenant_a
    )
    try:
        conn = await conectar_como("dashboard_service")
        tx = await _tx(conn, tenant_a)
        try:
            almacen = crear_almacen(conn)
            integracion = await almacen.guardar_profesional(profesional, {"calendar_id": "primary"}, "refresh-it-1")
            assert await almacen.buscar("google_calendar", profesional) == integracion
            assert (await almacen.obtener(integracion)).revelar() == "refresh-it-1"
            assert await almacen.eliminar_profesional(profesional) is True
            assert await almacen.buscar("google_calendar", profesional) is None
            assert await almacen.eliminar_profesional(profesional) is False
        finally:
            await tx.commit()
    finally:
        async with db_superuser.transaction():
            await db_superuser.execute("alter table public.audit_log disable trigger audit_log_solo_agregar_row")
            await db_superuser.execute("delete from public.audit_log where tenant_id = $1", tenant_a)
            await db_superuser.execute("alter table public.audit_log enable trigger audit_log_solo_agregar_row")
            await db_superuser.execute("delete from public.professionals where id = $1", profesional)
