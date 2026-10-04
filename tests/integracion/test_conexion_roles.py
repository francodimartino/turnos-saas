"""Conexión plataforma <-> turnos con conexiones reales de cada rol de login (§2.10, §3.2, §3.5).

Lo que depende de `session_user` (el actor de la anonimización, los rechazos de `calendar_scrub_targets`
por rol, el EXECUTE por rol) no se puede probar con `set role` en pgTAP: acá cada caller es una conexión
asyncpg autenticada como el rol. Los datos se confirman con el superusuario de test y se limpian al
terminar (audit_log es solo-agregar: el trigger se desactiva únicamente en la limpieza).
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.integracion

asyncpg = pytest.importorskip("asyncpg")

CLAVE_HMAC = "channel_user_hmac_key"
ROLES_SIN_EXECUTE = ["scheduler_job", "platform_admin_readonly"]


@pytest.fixture
async def clave_hmac(db_superuser):
    """Siembra la clave HMAC de Vault si no estaba (el script de setup real la pone una vez por proyecto)."""
    existente = await db_superuser.fetchval("select id from vault.secrets where name = $1", CLAVE_HMAC)
    creada = None
    if existente is None:
        creada = await db_superuser.fetchval(
            "select vault.create_secret($1, $2)", f"prueba-{uuid.uuid4().hex}", CLAVE_HMAC
        )
    try:
        yield
    finally:
        if creada is not None:
            await db_superuser.execute("delete from vault.secrets where id = $1", creada)


@pytest.fixture
async def escenario(db_superuser, clave_hmac):
    """Tenants A (activo), B (activo) y V (inactive vencido), con un cliente y turnos/pagos cada uno."""
    sufijo = uuid.uuid4().hex[:8]
    ids = {k: uuid.uuid4() for k in ("a", "b", "v")}
    hace_100_dias = datetime.now(timezone.utc) - timedelta(days=100)  # noqa: UP017
    await db_superuser.execute(
        "insert into public.tenants (id, name, slug) values ($1, 'A', $2), ($3, 'B', $4)",
        ids["a"],
        f"it-con-a-{sufijo}",
        ids["b"],
        f"it-con-b-{sufijo}",
    )
    await db_superuser.execute(
        "insert into public.tenants (id, name, slug, status, deactivated_at) values ($1, 'V', $2, 'inactive', $3)",
        ids["v"],
        f"it-con-v-{sufijo}",
        hace_100_dias,
    )
    datos = {"sufijo": sufijo, "tenant": ids}
    inicio = datetime.now(timezone.utc) + timedelta(days=30)  # noqa: UP017
    for clave, tenant in ids.items():
        prof, serv, cliente = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        pendiente, confirmado, pago = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        evento = f"evt-{clave}-{sufijo}"
        await db_superuser.execute(
            "insert into public.professionals (id, tenant_id, name) values ($1, $2, 'Profesional')", prof, tenant
        )
        await db_superuser.execute(
            "insert into public.services (id, tenant_id, name, duration_minutes, price) "
            "values ($1, $2, 'Corte', 30, 10)",
            serv,
            tenant,
        )
        await db_superuser.execute(
            "insert into public.end_customers (id, tenant_id, channel, channel_user_id, phone, name) "
            "values ($1, $2, 'telegram', $3, '+5491100000000', 'Nombre')",
            cliente,
            tenant,
            f"tg-{clave}-{sufijo}",
        )
        await db_superuser.execute(
            "insert into public.tenant_integrations (tenant_id, integration_type, professional_id) "
            "values ($1, 'google_calendar', $2)",
            tenant,
            prof,
        )
        await db_superuser.execute(
            "insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, "
            "status, pending_expires_at) values ($1, $2, $3, $4, $5, tstzrange($6, $7), 'pending', $8)",
            pendiente,
            tenant,
            cliente,
            serv,
            prof,
            inicio,
            inicio + timedelta(minutes=30),
            inicio - timedelta(days=1),
        )
        await db_superuser.execute(
            "insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, "
            "status, google_calendar_event_id) values ($1, $2, $3, $4, $5, tstzrange($6, $7), 'confirmed', $8)",
            confirmado,
            tenant,
            cliente,
            serv,
            prof,
            inicio + timedelta(days=1),
            inicio + timedelta(days=1, minutes=30),
            evento,
        )
        payload = json.dumps({"id": 1, "payer": {"email": "pagador@example.com"}})
        await db_superuser.execute(
            "insert into public.payments (id, tenant_id, payable_type, payable_id, provider_payment_id, amount, "
            "status, webhook_payload) values ($1, $2, 'booking_deposit', $3, $4, 10, 'approved', $5::jsonb)",
            pago,
            tenant,
            pendiente,
            f"mp-{clave}-{sufijo}",
            payload,
        )
        datos[clave] = {
            "prof": prof,
            "cliente": cliente,
            "pendiente": pendiente,
            "confirmado": confirmado,
            "pago": pago,
            "evento": evento,
            "mp": f"mp-{clave}-{sufijo}",
        }
    tenants = list(ids.values())
    try:
        yield datos
    finally:
        async with db_superuser.transaction():
            await db_superuser.execute("alter table public.audit_log disable trigger audit_log_solo_agregar_row")
            await db_superuser.execute("delete from public.audit_log where tenant_id = any($1::uuid[])", tenants)
            await db_superuser.execute("alter table public.audit_log enable trigger audit_log_solo_agregar_row")
            for tabla in (
                "payments",
                "bookings",
                "booking_packs",
                "notifications_log",
                "bot_sessions",
                "tenant_integrations",
                "professionals",
                "services",
                "end_customers",
            ):
                await db_superuser.execute(f"delete from public.{tabla} where tenant_id = any($1::uuid[])", tenants)
            await db_superuser.execute("delete from public.tenants where id = any($1::uuid[])", tenants)


async def _contexto(conn, tenant_id=None):
    """Abre una transacción y setea el contexto de tenant como lo hace platform_tenancy."""
    tx = conn.transaction()
    await tx.start()
    if tenant_id is not None:
        await conn.execute("select set_config('app.current_tenant_id', $1, true)", str(tenant_id))
    return tx


# ---------------------------------------------------------------------------
# calendar_scrub_targets: rechazos por rol y por tenant
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rol", ["bot_service", "dashboard_service"])
async def test_calendar_scrub_targets_devuelve_los_eventos_de_su_tenant(conectar_como, escenario, rol):
    a = escenario["a"]
    conn = await conectar_como(rol)
    tx = await _contexto(conn, escenario["tenant"]["a"])
    try:
        filas = await conn.fetch(
            "select integration_id, google_calendar_event_id from public.calendar_scrub_targets($1, $2)",
            escenario["tenant"]["a"],
            a["cliente"],
        )
        todas = await conn.fetch(
            "select google_calendar_event_id from public.calendar_scrub_targets($1)", escenario["tenant"]["a"]
        )
    finally:
        await tx.commit()
    assert [f["google_calendar_event_id"] for f in filas] == [a["evento"]]
    assert filas[0]["integration_id"] is not None
    assert [f["google_calendar_event_id"] for f in todas] == [a["evento"]]


@pytest.mark.parametrize("rol", ["bot_service", "dashboard_service"])
async def test_calendar_scrub_targets_rechaza_un_tenant_ajeno_al_contexto(conectar_como, escenario, rol):
    conn = await conectar_como(rol)
    tx = await _contexto(conn, escenario["tenant"]["a"])
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError, match="tenant"):
            await conn.fetch("select * from public.calendar_scrub_targets($1)", escenario["tenant"]["b"])
    finally:
        await tx.rollback()


async def test_calendar_scrub_targets_rechaza_sin_contexto_de_tenant(conectar_como, escenario):
    conn = await conectar_como("bot_service")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await conn.fetch("select * from public.calendar_scrub_targets($1)", escenario["tenant"]["a"])


async def test_calendar_scrub_targets_de_purge_job_revalida_inactive_y_vencido(conectar_como, escenario):
    conn = await conectar_como("purge_job")
    with pytest.raises(asyncpg.CheckViolationError, match="purga rechazada"):
        await conn.fetch("select * from public.calendar_scrub_targets($1)", escenario["tenant"]["a"])
    filas = await conn.fetch("select * from public.calendar_scrub_targets($1)", escenario["tenant"]["v"])
    assert [f["google_calendar_event_id"] for f in filas] == [escenario["v"]["evento"]]


async def test_calendar_scrub_targets_de_platform_admin_cruza_tenants(conectar_como, escenario):
    conn = await conectar_como("platform_admin")
    filas = await conn.fetch("select * from public.calendar_scrub_targets($1)", escenario["tenant"]["b"])
    assert [f["google_calendar_event_id"] for f in filas] == [escenario["b"]["evento"]]


@pytest.mark.parametrize("rol", ROLES_SIN_EXECUTE)
async def test_calendar_scrub_targets_sin_execute_para_otros_roles(conectar_como, escenario, rol):
    conn = await conectar_como(rol)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await conn.fetch("select * from public.calendar_scrub_targets($1)", escenario["tenant"]["a"])


async def test_la_core_no_es_ejecutable_por_los_roles_de_aplicacion(conectar_como, escenario):
    conn = await conectar_como("bot_service")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await conn.fetch("select * from public.calendar_scrub_targets_core($1, null, 'bot')", escenario["tenant"]["a"])


# ---------------------------------------------------------------------------
# anonymize_end_customer (pública) con las ramas de dominio, como bot_service
# ---------------------------------------------------------------------------


async def test_anonimizar_limpia_payer_y_cancela_pendientes_futuros(conectar_como, db_superuser, escenario):
    a, b = escenario["a"], escenario["b"]
    conn = await conectar_como("bot_service")
    tx = await _contexto(conn, escenario["tenant"]["a"])
    try:
        await conn.execute("select public.anonymize_end_customer($1)", a["cliente"])
    finally:
        await tx.commit()

    payload = json.loads(
        await db_superuser.fetchval("select webhook_payload::text from public.payments where id = $1", a["pago"])
    )
    assert payload == {"id": 1}
    assert (
        await db_superuser.fetchval("select provider_payment_id from public.payments where id = $1", a["pago"])
        == a["mp"]
    )
    estados = {
        fila["id"]: fila["status"]
        for fila in await db_superuser.fetch(
            "select id, status from public.bookings where id = any($1::uuid[])", [a["pendiente"], a["confirmado"]]
        )
    }
    assert estados == {a["pendiente"]: "cancelled", a["confirmado"]: "confirmed"}
    # otro tenant intacto
    assert await db_superuser.fetchval("select status from public.bookings where id = $1", b["pendiente"]) == "pending"
    otro = await db_superuser.fetchval("select webhook_payload::text from public.payments where id = $1", b["pago"])
    assert "payer" in json.loads(otro)
    campos = await db_superuser.fetchval(
        "select metadata::text from public.audit_log where action = 'anonymize_end_customer' and entity_id = $1",
        a["cliente"],
    )
    assert "payments.payer" in campos and "example.com" not in campos


# ---------------------------------------------------------------------------
# payments: el tenant_id real sale del padre, con el rol real del backend
# ---------------------------------------------------------------------------


async def test_payments_corrige_el_tenant_id_y_rechaza_padres_de_otro_tenant(conectar_como, db_superuser, escenario):
    a, b = escenario["a"], escenario["b"]
    nuevo = uuid.uuid4()
    conn = await conectar_como("bot_service")
    tx = await _contexto(conn, escenario["tenant"]["a"])
    try:
        # tenant_id deliberadamente distinto (B) con un booking propio (A): el trigger lo corrige a A
        await conn.execute(
            "insert into public.payments (id, tenant_id, payable_type, payable_id, amount, status) "
            "values ($1, $2, 'booking_deposit', $3, 5, 'pending')",
            nuevo,
            escenario["tenant"]["b"],
            a["confirmado"],
        )
    finally:
        await tx.commit()
    assert (
        await db_superuser.fetchval("select tenant_id from public.payments where id = $1", nuevo)
        == escenario["tenant"]["a"]
    )

    tx = await _contexto(conn, escenario["tenant"]["a"])
    try:
        # un booking de otro tenant no es visible para el bot de A: el pago se rechaza
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(
                "insert into public.payments (tenant_id, payable_type, payable_id, amount, status) "
                "values ($1, 'booking_deposit', $2, 5, 'pending')",
                escenario["tenant"]["a"],
                b["confirmado"],
            )
    finally:
        await tx.rollback()
