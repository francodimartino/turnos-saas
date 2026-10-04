"""Cascada de purga contra Postgres real, conectada como el rol `purge_job` (sin grants sobre tablas).

La Auth Admin API y Google Calendar son falsos: la API de Auth borra de `auth.users` con el superusuario de test
(lo mismo que haría la real, y la FK `on delete set null` deja `tenant_users.auth_user_id` en null) y el calendario
registra los pares que recibe. Todo lo demás es la base real: `list_tenants_due_for_purge`, `calendar_scrub_targets`,
`purge_tenant_data`, `mark_tenant_purged`, `prune_inbound_messages` y `write_audit_log`.
"""

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from platform_calendar import EventoNoLimpiado

from platform_admin import ErrorEliminacionCuenta, purgar_tenants_vencidos

pytestmark = pytest.mark.integracion

asyncpg = pytest.importorskip("asyncpg")

CLAVE_HMAC = "channel_user_hmac_key"


class PoolDeUnaConexion:
    def __init__(self, conn):
        self._conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self._conn


class AuthFalso:
    def __init__(self, db_superuser):
        self._db = db_superuser
        self.falla = False
        self.pedidos: list[uuid.UUID] = []

    async def eliminar_usuario(self, auth_user_id):
        self.pedidos.append(auth_user_id)
        if self.falla:
            raise ErrorEliminacionCuenta("Auth Admin API respondió 500")
        borrado = await self._db.fetchval("delete from auth.users where id = $1 returning id", auth_user_id)
        return borrado is not None


class CalendarioFalso:
    def __init__(self):
        self.pares: list = []

    def para(self, conn):
        return self

    async def limpiar_eventos(self, pares):
        self.pares.append(list(pares))
        return [EventoNoLimpiado(i, e, "token_vencido") for i, e in pares]


class ReportadorFalso:
    def __init__(self):
        self.excepciones: list[tuple] = []
        self.mensajes: list[tuple] = []

    def capturar_excepcion(self, exc, **contexto):
        self.excepciones.append((exc, contexto))

    def capturar_mensaje(self, mensaje, **contexto):
        self.mensajes.append((mensaje, contexto))

    def de(self, tenant_id):
        return [m for m in self.mensajes if m[1].get("tenant_id") == str(tenant_id)]


@pytest.fixture
async def clave_hmac(db_superuser):
    """Siembra la clave HMAC de Vault si no estaba (anonymize_end_customer la necesita; el setup real la pone)."""
    existente = await db_superuser.fetchval("select id from vault.secrets where name = $1", CLAVE_HMAC)
    creada = None
    if existente is None:
        creada = await db_superuser.fetchval(
            "select vault.create_secret($1, $2)", "clave-de-prueba-integracion", CLAVE_HMAC
        )
    try:
        yield
    finally:
        if creada is not None:
            await db_superuser.execute("delete from vault.secrets where id = $1", creada)


@pytest.fixture
async def escenario(db_superuser, clave_hmac):
    """V: inactive y vencido (100 días), con owner con cuenta y un evento de calendar. A: activo con cuenta.
    R: inactive pero con el período sin vencer."""
    sufijo = uuid.uuid4().hex[:8]
    v, a, r = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    cuentas = {"v": uuid.uuid4(), "a": uuid.uuid4(), "r": uuid.uuid4()}
    hace_100_dias = datetime.now(timezone.utc) - timedelta(days=100)  # noqa: UP017
    await db_superuser.execute(
        "insert into public.tenants (id, name, slug) values ($1, 'A', $2)", a, f"it-pg-a-{sufijo}"
    )
    await db_superuser.execute(
        "insert into public.tenants (id, name, slug, status, deactivated_at) values ($1, 'V', $2, 'inactive', $3)",
        v,
        f"it-pg-v-{sufijo}",
        hace_100_dias,
    )
    await db_superuser.execute(
        "insert into public.tenants (id, name, slug, status, deactivated_at) values ($1, 'R', $2, 'inactive', now())",
        r,
        f"it-pg-r-{sufijo}",
    )
    for clave, tenant in (("v", v), ("a", a), ("r", r)):
        await db_superuser.execute(
            "insert into auth.users (id, email) values ($1, $2)", cuentas[clave], f"{clave}.{sufijo}@example.test"
        )
        await db_superuser.execute(
            "insert into public.tenant_users (tenant_id, is_owner, auth_user_id) values ($1, true, $2)",
            tenant,
            cuentas[clave],
        )
    prof, serv, cliente, reserva = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await db_superuser.execute(
        "insert into public.professionals (id, tenant_id, name) values ($1, $2, 'Profesional')", prof, v
    )
    integracion = await db_superuser.fetchval(
        "insert into public.tenant_integrations (tenant_id, integration_type, professional_id) "
        "values ($1, 'google_calendar', $2) returning id",
        v,
        prof,
    )
    await db_superuser.execute(
        "insert into public.services (id, tenant_id, name, duration_minutes, price) values ($1, $2, 'Corte', 30, 10)",
        serv,
        v,
    )
    await db_superuser.execute(
        "insert into public.end_customers (id, tenant_id, channel, channel_user_id, phone, name) "
        "values ($1, $2, 'telegram', $3, '+5491100000000', 'Nombre')",
        cliente,
        v,
        f"tg-{sufijo}",
    )
    inicio = datetime.now(timezone.utc) + timedelta(days=30)  # noqa: UP017
    await db_superuser.execute(
        "insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, "
        "status, google_calendar_event_id) values ($1, $2, $3, $4, $5, tstzrange($6, $7), 'confirmed', $8)",
        reserva,
        v,
        cliente,
        serv,
        prof,
        inicio,
        inicio + timedelta(minutes=30),
        f"evt-{sufijo}",
    )
    await db_superuser.execute(
        "insert into public.inbound_messages (tenant_id, channel, external_message_id, received_at) "
        "values ($1, 'telegram', $2, now() - interval '9 days'), ($1, 'telegram', $3, now())",
        a,
        f"it-viejo-{sufijo}",
        f"it-nuevo-{sufijo}",
    )
    tenants = [v, a, r]
    try:
        yield {
            "v": v,
            "a": a,
            "r": r,
            "cuentas": cuentas,
            "integracion": integracion,
            "evento": f"evt-{sufijo}",
            "sufijo": sufijo,
        }
    finally:
        async with db_superuser.transaction():
            # Limpieza sin triggers (audit_log solo-agregar, último owner): solo vale dentro de esta transacción
            await db_superuser.execute("set local session_replication_role = replica")
            await db_superuser.execute("delete from public.audit_log where tenant_id = any($1::uuid[])", tenants)
            for tabla in (
                "bookings",
                "tenant_users",
                "tenant_integrations",
                "professionals",
                "services",
                "end_customers",
                "bot_sessions",
                "inbound_messages",
            ):
                await db_superuser.execute(f"delete from public.{tabla} where tenant_id = any($1::uuid[])", tenants)
            await db_superuser.execute("delete from public.tenants where id = any($1::uuid[])", tenants)
            await db_superuser.execute("delete from auth.users where id = any($1::uuid[])", list(cuentas.values()))


@pytest.fixture
async def entorno(conectar_como, db_superuser):
    conn = await conectar_como("purge_job")
    return PoolDeUnaConexion(conn), AuthFalso(db_superuser), CalendarioFalso(), ReportadorFalso(), conn


async def _correr(entorno, log=None):
    pool, auth, calendario, reportador, _ = entorno
    return await purgar_tenants_vencidos(pool, auth, calendario.para, reportador=reportador, log_paso=log)


async def test_cascada_completa_como_purge_job(entorno, db_superuser, escenario):
    pool, auth, calendario, reportador, conn = entorno
    logs: list[tuple] = []
    assert await conn.fetchval("select session_user") == "purge_job"

    resultado = await _correr(entorno, lambda evento, **campos: logs.append((evento, campos)))

    v, a, r = escenario["v"], escenario["a"], escenario["r"]
    por_tenant = {t.tenant_id: t for t in resultado.tenants}
    assert por_tenant[v].estado == "purged", reportador.excepciones
    assert a not in por_tenant and r not in por_tenant  # activo y sin vencer: ni se listan

    fila = await db_superuser.fetchrow("select status, slug from public.tenants where id = $1", v)
    assert fila["status"] == "purged" and "-purged-" in fila["slug"]
    assert await db_superuser.fetchval("select status from public.tenants where id = $1", a) == "active"
    assert await db_superuser.fetchval("select status from public.tenants where id = $1", r) == "inactive"
    # solo se borró la cuenta del tenant purgado
    existentes = await db_superuser.fetchval(
        "select count(*) from auth.users where id = any($1::uuid[])", list(escenario["cuentas"].values())
    )
    assert existentes == 2
    assert auth.pedidos.count(escenario["cuentas"]["v"]) == 1
    assert escenario["cuentas"]["a"] not in auth.pedidos and escenario["cuentas"]["r"] not in auth.pedidos
    assert (
        await db_superuser.fetchval(
            "select count(*) from public.tenant_users where tenant_id = $1 and auth_user_id is not null", v
        )
        == 0
    )

    # paso 0: el evento se pidió limpiar ANTES de borrar credenciales y el fallo quedó en audit como system
    assert calendario.pares == [[(escenario["integracion"], escenario["evento"])]]
    fallos = await db_superuser.fetch(
        "select actor_type, metadata from public.audit_log where tenant_id = $1 and action = 'calendar_scrub_failed'",
        v,
    )
    assert [f["actor_type"] for f in fallos] == ["system"] and escenario["evento"] in fallos[0]["metadata"]

    # cada paso deja audit tenant_purge_step (los de la base y los de la cascada) y un log estructurado
    pasos = {
        f["paso"]
        for f in await db_superuser.fetch(
            "select metadata ->> 'step' as paso from public.audit_log "
            "where tenant_id = $1 and action = 'tenant_purge_step'",
            v,
        )
    }
    assert pasos >= {"calendar_scrub", "anonymize_end_customers", "delete_auth_accounts", "mark_tenant_purged"}
    assert {c["step"] for e, c in logs if e == "tenant_purge_step" and c["tenant_id"] == str(v)} == {
        "calendar_scrub",
        "purge_tenant_data",
        "delete_auth_accounts",
        "mark_tenant_purged",
    }

    # la misma corrida poda inbound_messages viejos; el reciente queda
    assert resultado.mensajes_podados >= 1
    restantes = {
        f["external_message_id"]
        for f in await db_superuser.fetch(
            "select external_message_id from public.inbound_messages where tenant_id = $1", a
        )
    }
    assert restantes == {f"it-nuevo-{escenario['sufijo']}"}
    assert reportador.de(v) == []


async def test_interrumpida_en_la_eliminacion_de_cuentas_la_corrida_siguiente_llega_a_purged(
    entorno, db_superuser, escenario
):
    _, auth, _, reportador, _ = entorno
    v = escenario["v"]
    auth.falla = True

    primera = await _correr(entorno)
    assert {t.tenant_id: t for t in primera.tenants}[v].paso_fallido == "delete_auth_accounts", reportador.excepciones
    assert await db_superuser.fetchval("select status from public.tenants where id = $1", v) == "inactive"
    assert [c["paso"] for e, c in reportador.excepciones if c.get("tenant_id") == str(v)] == ["delete_auth_accounts"]
    # 10 días de atraso (> 3) sin purgar: se reporta como mensaje además de la excepción
    assert len(reportador.de(v)) == 1 and reportador.de(v)[0][1]["dias_vencido"] == 10

    auth.falla = False
    segunda = await _correr(entorno)
    assert {t.tenant_id: t for t in segunda.tenants}[v].estado == "purged"
    assert await db_superuser.fetchval("select status from public.tenants where id = $1", v) == "purged"
    assert len(reportador.de(v)) == 1  # la segunda corrida no reporta de nuevo


async def test_ejecutar_la_cascada_a_mano_sobre_un_activo_falla_y_no_borra_nada(entorno, db_superuser, escenario):
    from platform_admin import PasoPurgaFallido, purgar_tenant

    pool, auth, calendario, _, _ = entorno
    with pytest.raises(PasoPurgaFallido) as exc:
        await purgar_tenant(pool, escenario["a"], auth, calendario.para)
    assert exc.value.paso == "calendar_scrub"
    assert await db_superuser.fetchval("select status from public.tenants where id = $1", escenario["a"]) == "active"
    assert auth.pedidos == []
