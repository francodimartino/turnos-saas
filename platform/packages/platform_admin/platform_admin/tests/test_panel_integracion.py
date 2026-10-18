"""platform_admin (2/2) contra Postgres real, conectado como el rol `platform_admin`.

Cubre settings, visor de audit_log, facturación, visor de notificaciones con problemas y carga de integraciones.
"""

import json
import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from platform_secrets import crear_almacen

from platform_admin import (
    IntegracionNoSoportada,
    ReduccionSinConfirmar,
    SettingInvalido,
    cargar_integracion_tenant,
    editar_facturacion,
    editar_setting,
    listar_audit_log,
    listar_limpiezas_calendar_fallidas,
    listar_notificaciones_con_problemas,
    listar_settings,
    suspender,
    visor_problemas_notificaciones,
)

pytestmark = pytest.mark.integracion

asyncpg = pytest.importorskip("asyncpg")

UTC = timezone.utc  # noqa: UP017
ACTOR = uuid.uuid4()
CLAVE = "tenant_deletion_grace_period_days"


class PoolDeUnaConexion:
    """Adapta una conexión de `conectar_como` a la interfaz de pool que usa `platform_tenancy`."""

    def __init__(self, conn):
        self._conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self._conn


async def _borrar_sin_triggers(db, sentencias):
    async with db.transaction():
        # Limpieza sin triggers (audit_log solo-agregar): solo vale dentro de esta transacción
        await db.execute("set local session_replication_role = replica")
        for sql, args in sentencias:
            await db.execute(sql, *args)


@pytest.fixture
async def pool(conectar_como):
    return PoolDeUnaConexion(await conectar_como("platform_admin"))


@pytest.fixture
async def tenant(db_superuser):
    """Un tenant sembrado por el superusuario; limpia su audit, integraciones, secrets y notificaciones."""
    tenant_id = uuid.uuid4()
    await db_superuser.execute(
        "insert into public.tenants (id, name, slug) values ($1, 'Panel', $2)",
        tenant_id,
        f"it-panel-{uuid.uuid4().hex[:10]}",
    )
    try:
        yield tenant_id
    finally:
        secretos = await db_superuser.fetch(
            "select secret_id from public.tenant_integrations where tenant_id = $1 and secret_id is not null",
            tenant_id,
        )
        await _borrar_sin_triggers(
            db_superuser,
            [
                ("delete from public.notifications_log where tenant_id = $1", (tenant_id,)),
                ("delete from public.end_customers where tenant_id = $1", (tenant_id,)),
                ("delete from public.audit_log where tenant_id = $1", (tenant_id,)),
                ("delete from public.tenant_integrations where tenant_id = $1", (tenant_id,)),
                ("delete from vault.secrets where id = any($1::uuid[])", ([f["secret_id"] for f in secretos],)),
                ("delete from public.tenants where id = $1", (tenant_id,)),
            ],
        )


@pytest.fixture
async def restaurar_periodo_de_gracia(db_superuser):
    original = await db_superuser.fetchval("select value::text from public.platform_settings where key = $1", CLAVE)
    yield
    await db_superuser.execute("update public.platform_settings set value = $2::jsonb where key = $1", CLAVE, original)
    await _borrar_sin_triggers(
        db_superuser, [("delete from public.audit_log where actor_id = $1 and tenant_id is null", (ACTOR,))]
    )


# --- platform_settings ---------------------------------------------------------------------------------


async def test_editar_periodo_de_gracia_persiste_y_audita_con_tenant_null(
    pool, db_superuser, restaurar_periodo_de_gracia
):
    await db_superuser.execute("update public.platform_settings set value = '90'::jsonb where key = $1", CLAVE)
    res = await editar_setting(pool, CLAVE, 45, actor_id=ACTOR, confirmar_reduccion=True)

    assert res["hasta"] == 45
    assert (await listar_settings(pool, actor_id=ACTOR))[CLAVE] == 45
    fila = await db_superuser.fetchrow(
        "select tenant_id, actor_type, entity, metadata from public.audit_log "
        "where action = 'platform_settings_update' and actor_id = $1",
        ACTOR,
    )
    assert fila["tenant_id"] is None and fila["actor_type"] == "platform_admin"
    assert json.loads(fila["metadata"])["hasta"] == 45


async def test_bajar_el_periodo_de_gracia_sin_confirmar_no_escribe_y_subirlo_no_lo_pide(
    pool, db_superuser, restaurar_periodo_de_gracia
):
    await db_superuser.execute("update public.platform_settings set value = '90'::jsonb where key = $1", CLAVE)
    with pytest.raises(ReduccionSinConfirmar):
        await editar_setting(pool, CLAVE, 60, actor_id=ACTOR)
    assert (await listar_settings(pool, actor_id=ACTOR))[CLAVE] == 90
    assert (await editar_setting(pool, CLAVE, 120, actor_id=ACTOR))["hasta"] == 120


async def test_periodo_de_gracia_invalido_no_cambia_nada(pool, db_superuser, restaurar_periodo_de_gracia):
    antes = await db_superuser.fetchval("select value::text from public.platform_settings where key = $1", CLAVE)
    for invalido in (0, 1, 29, -3, "90", 1.5, True):
        with pytest.raises(SettingInvalido):
            await editar_setting(pool, CLAVE, invalido, actor_id=ACTOR, confirmar_reduccion=True)
    assert (
        await db_superuser.fetchval("select value::text from public.platform_settings where key = $1", CLAVE) == antes
    )


# --- visor de audit_log por tenant ------------------------------------------------------------------------


async def test_visor_de_audit_muestra_acciones_de_platform_admin_y_de_otros_actores(pool, db_superuser, tenant):
    await suspender(pool, tenant, actor_id=ACTOR)
    await db_superuser.execute(
        "insert into public.audit_log (tenant_id, actor_type, action) values ($1, 'staff', 'booking_confirm')", tenant
    )

    todas = await listar_audit_log(pool, tenant, actor_id=ACTOR)
    assert {(e.actor_type, e.action) for e in todas} == {
        ("platform_admin", "tenant_suspend"),
        ("staff", "booking_confirm"),
    }
    solo_admin = await listar_audit_log(pool, tenant, actor_type="platform_admin")
    assert [e.action for e in solo_admin] == ["tenant_suspend"]
    assert solo_admin[0].metadata == {"desde": "active", "hasta": "suspended"}
    assert len(await listar_audit_log(pool, tenant, limit=1)) == 1


# --- facturación -----------------------------------------------------------------------------------------


async def test_editar_facturacion_persiste_y_audita_sin_las_notas(pool, db_superuser, tenant):
    nota = f"pagó por transferencia {uuid.uuid4().hex}"
    await editar_facturacion(pool, tenant, paid_until=datetime(2026, 12, 31).date(), billing_notes=nota, actor_id=ACTOR)

    fila = await db_superuser.fetchrow("select paid_until, billing_notes from public.tenants where id = $1", tenant)
    assert str(fila["paid_until"]) == "2026-12-31" and fila["billing_notes"] == nota
    auditoria = await db_superuser.fetchrow(
        "select actor_type, metadata::text as m from public.audit_log where tenant_id = $1 "
        "and action = 'tenant_billing_update'",
        tenant,
    )
    assert auditoria["actor_type"] == "platform_admin" and nota not in auditoria["m"]


# --- visor de problemas ----------------------------------------------------------------------------------


async def _insertar_audit(db, tenant, accion, evento):
    await db.execute(
        "insert into public.audit_log (tenant_id, actor_type, action, entity, metadata) "
        "values ($1, 'system', $2, 'tenants', $3::jsonb)",
        tenant,
        accion,
        json.dumps({"event_id": evento}),
    )


async def test_limpiezas_de_calendar_una_entrada_por_evento_con_su_estado(pool, db_superuser, tenant):
    ev_ok, ev_pend = f"ev-{uuid.uuid4().hex}", f"ev-{uuid.uuid4().hex}"
    for accion, evento in [
        ("calendar_scrub_failed", ev_ok),
        ("calendar_scrub_resolved", ev_ok),
        ("calendar_scrub_failed", ev_pend),
        ("calendar_scrub_retry_failed", ev_pend),
        ("tenant_purge_step", ev_pend),
    ]:
        await _insertar_audit(db_superuser, tenant, accion, evento)

    filas = [f for f in await listar_limpiezas_calendar_fallidas(pool, actor_id=ACTOR) if f.tenant_id == tenant]
    por_evento = {f.metadata["event_id"]: f for f in filas}
    assert len(filas) == 2 and set(por_evento) == {ev_ok, ev_pend}
    assert por_evento[ev_ok].action == "calendar_scrub_resolved"  # fallo y despues resuelto: resuelta
    assert por_evento[ev_pend].action == "calendar_scrub_retry_failed"  # una sola entrada pendiente
    assert por_evento[ev_pend].metadata["intentos"] == 2
    assert filas[0].metadata["event_id"] == ev_pend  # las pendientes van primero


async def test_limpiezas_de_calendar_el_tope_no_deja_afuera_pendientes(pool, db_superuser, tenant):
    pend = f"ev-{uuid.uuid4().hex}"
    await _insertar_audit(db_superuser, tenant, "calendar_scrub_failed", pend)
    for _ in range(3):  # resueltas mas nuevas que la pendiente
        ev = f"ev-{uuid.uuid4().hex}"
        await _insertar_audit(db_superuser, tenant, "calendar_scrub_failed", ev)
        await _insertar_audit(db_superuser, tenant, "calendar_scrub_resolved", ev)
    todas = await listar_limpiezas_calendar_fallidas(pool, actor_id=ACTOR, limit=500)
    mias = [f for f in todas if f.tenant_id == tenant]
    assert mias[0].metadata["event_id"] == pend and mias[0].action == "calendar_scrub_failed"


async def test_limpiezas_de_calendar_descartada_va_despues_de_las_pendientes(pool, db_superuser, tenant):
    desc, pend = f"ev-{uuid.uuid4().hex}", f"ev-{uuid.uuid4().hex}"
    await _insertar_audit(db_superuser, tenant, "calendar_scrub_failed", desc)
    await _insertar_audit(db_superuser, tenant, "calendar_scrub_discarded", desc)
    await _insertar_audit(db_superuser, tenant, "calendar_scrub_failed", pend)
    todas = await listar_limpiezas_calendar_fallidas(pool, actor_id=ACTOR, limit=500)
    mias = [f for f in todas if f.tenant_id == tenant]
    por_evento = {f.metadata["event_id"]: f for f in mias}
    assert por_evento[desc].action == "calendar_scrub_discarded" and por_evento[desc].metadata["intentos"] == 0
    assert mias[0].metadata["event_id"] == pend  # la descartada no desplaza a la pendiente


async def _tiene_created_at(db) -> bool:
    return bool(
        await db.fetchval(
            "select 1 from information_schema.columns "
            "where table_schema = 'public' and table_name = 'notifications_log' and column_name = 'created_at'"
        )
    )


async def test_visor_de_notificaciones_con_problemas_en_cuatro_estados(pool, db_superuser, tenant):
    if not await _tiene_created_at(db_superuser):
        pytest.skip(
            "notifications_log todavía no tiene created_at (migración pendiente): "
            "sin esa marca no se puede medir pending hace más de 1 hora"
        )
    cliente = await db_superuser.fetchval(
        "insert into public.end_customers (tenant_id) values ($1) returning id", tenant
    )
    ahora = datetime.now(UTC)
    ids = {}
    for estado, status, antiguedad in (
        ("failed", "failed", timedelta(minutes=5)),
        ("pending_viejo", "pending", timedelta(hours=2)),
        ("pending_reciente", "pending", timedelta(minutes=10)),
        ("sent", "sent", timedelta(hours=3)),
    ):
        ids[estado] = await db_superuser.fetchval(
            "insert into public.notifications_log "
            "(tenant_id, end_customer_id, channel, template, status, created_at) "
            "values ($1, $2, 'telegram', 'booking_approved', $3, $4) returning id",
            tenant,
            cliente,
            status,
            ahora - antiguedad,
        )
    scrub = await db_superuser.fetchval(
        "insert into public.audit_log (tenant_id, actor_type, action) values ($1, 'system', 'calendar_scrub_failed') "
        "returning id",
        tenant,
    )

    visor = await visor_problemas_notificaciones(pool, actor_id=ACTOR)
    mias = {n.id for n in visor.notificaciones if n.tenant_id == tenant}
    assert mias == {ids["failed"], ids["pending_viejo"]}
    assert scrub in {e.id for e in visor.limpiezas_calendar}
    assert {n.id for n in await listar_notificaciones_con_problemas(pool) if n.tenant_id == tenant} == mias


# --- carga de integraciones ------------------------------------------------------------------------------


def _credencial() -> str:
    return json.dumps({"bot_token": f"123456:{secrets.token_urlsafe(24)}", "webhook_secret": secrets.token_urlsafe(32)})


async def _leer_secret(conectar_como, tenant_id, integration_id) -> str:
    conn = await conectar_como("bot_service")
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute("select set_config('app.current_tenant_id', $1, true)", str(tenant_id))
        return (await crear_almacen(conn).obtener(integration_id)).revelar()
    finally:
        await tx.rollback()


async def test_cargar_telegram_guarda_en_vault_genera_webhook_key_y_no_devuelve_el_secret(
    pool, conectar_como, db_superuser, tenant
):
    credencial = _credencial()
    res = await cargar_integracion_tenant(pool, tenant, "telegram_bot", {}, credencial, actor_id=ACTOR)

    assert str(uuid.UUID(res.webhook_key)) == res.webhook_key
    assert credencial not in repr(res)
    fila = await db_superuser.fetchrow(
        "select config::text as c, professional_id, secret_id from public.tenant_integrations where id = $1",
        res.integration_id,
    )
    assert json.loads(fila["c"]) == {"webhook_key": res.webhook_key, "bot_id": "123456"}
    assert fila["professional_id"] is None and fila["secret_id"] is not None
    assert credencial not in fila["c"]
    assert await _leer_secret(conectar_como, tenant, res.integration_id) == credencial
    auditoria = await db_superuser.fetchrow(
        "select actor_type, actor_id, metadata::text as m from public.audit_log "
        "where tenant_id = $1 and action = 'store_tenant_integration'",
        tenant,
    )
    assert auditoria["actor_type"] == "platform_admin" and auditoria["actor_id"] == ACTOR
    assert credencial not in auditoria["m"]


async def test_recargar_conserva_el_webhook_key_y_rota_la_credencial(pool, conectar_como, tenant):
    primera = await cargar_integracion_tenant(pool, tenant, "telegram_bot", {}, _credencial(), actor_id=ACTOR)
    nueva = _credencial()
    segunda = await cargar_integracion_tenant(pool, tenant, "telegram_bot", {}, nueva, actor_id=ACTOR)

    assert segunda.integration_id == primera.integration_id and segunda.webhook_key == primera.webhook_key
    assert await _leer_secret(conectar_como, tenant, segunda.integration_id) == nueva


async def test_integraciones_por_profesional_y_whatsapp_se_rechazan_sin_tocar_la_base(pool, db_superuser, tenant):
    for tipo in ("google_calendar", "whatsapp_business", "mercadopago"):
        with pytest.raises(IntegracionNoSoportada):
            await cargar_integracion_tenant(pool, tenant, tipo, {}, _credencial(), actor_id=ACTOR)
    assert (
        await db_superuser.fetchval("select count(*) from public.tenant_integrations where tenant_id = $1", tenant) == 0
    )
