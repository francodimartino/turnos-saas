"""Job de purga de punta a punta contra Postgres real, conectado como `purge_job` (sin grants sobre tablas).

La Auth Admin API es falsa (borra de `auth.users` con el superusuario de test, lo mismo que haría la real) y el
Google Calendar no está configurado (sin variables OAuth): el paso 0 tiene que registrar `calendar_scrub_failed`
por configuración y la purga seguir. Todo lo demás es la base real.
"""

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from platform_admin import ErrorEliminacionCuenta

from jobs_purga import ejecutar, ejecutar_con_monitor, exit_code, fabrica_calendario_desde_entorno

pytestmark = pytest.mark.integracion

asyncpg = pytest.importorskip("asyncpg")

CLAVE_HMAC = "channel_user_hmac_key"
VARIABLES_GOOGLE = (
    "GOOGLE_OAUTH_CLIENT_ID",
    "GOOGLE_OAUTH_CLIENT_SECRET",
    "GOOGLE_OAUTH_REDIRECT_URI",
    "CALENDAR_OAUTH_STATE_SECRET",
)


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


class ReportadorFalso:
    def __init__(self):
        self.excepciones: list[tuple] = []
        self.mensajes: list[tuple] = []

    def capturar_excepcion(self, exc, **contexto):
        self.excepciones.append((exc, contexto))

    def capturar_mensaje(self, mensaje, **contexto):
        self.mensajes.append((mensaje, contexto))


class Checkins:
    def __init__(self):
        self.estados: list[str] = []

    def __call__(self, **kwargs):
        self.estados.append(kwargs["status"])
        return "check-in-de-prueba"


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
    nuevo = uuid.uuid4()  # tenant que vuelve a contratar con el slug liberado
    cuentas = {"v": uuid.uuid4(), "a": uuid.uuid4(), "r": uuid.uuid4()}
    slug_v = f"it-job-v-{sufijo}"
    hace_100_dias = datetime.now(timezone.utc) - timedelta(days=100)  # noqa: UP017
    await db_superuser.execute(
        "insert into public.tenants (id, name, slug) values ($1, 'A', $2)", a, f"it-job-a-{sufijo}"
    )
    await db_superuser.execute(
        "insert into public.tenants (id, name, slug, status, deactivated_at) values ($1, 'V', $2, 'inactive', $3)",
        v,
        slug_v,
        hace_100_dias,
    )
    await db_superuser.execute(
        "insert into public.tenants (id, name, slug, status, deactivated_at) values ($1, 'R', $2, 'inactive', now())",
        r,
        f"it-job-r-{sufijo}",
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
    await db_superuser.execute(
        "insert into public.tenant_integrations (tenant_id, integration_type, professional_id) "
        "values ($1, 'google_calendar', $2)",
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
    tenants = [v, a, r, nuevo]
    try:
        yield {
            "v": v,
            "a": a,
            "r": r,
            "nuevo": nuevo,
            "slug_v": slug_v,
            "cuentas": cuentas,
            "evento": f"evt-{sufijo}",
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
async def entorno(conectar_como, db_superuser, monkeypatch):
    for variable in VARIABLES_GOOGLE:
        monkeypatch.delenv(variable, raising=False)  # Google Calendar sin configurar
    conn = await conectar_como("purge_job")
    return PoolDeUnaConexion(conn), AuthFalso(db_superuser), ReportadorFalso(), Checkins(), conn


async def _correr_job(entorno):
    """Lo mismo que hace `python -m jobs_purga`, con pool/Auth/reportador/monitor de prueba. Devuelve el exit code."""
    pool, auth, reportador, checkins, _ = entorno

    async def operacion():
        return await ejecutar(pool, auth, fabrica_calendario_desde_entorno(None), reportador)

    return exit_code(await ejecutar_con_monitor(operacion, checkin=checkins))


async def _tenant(db, tenant_id):
    return dict(await db.fetchrow("select status, slug from public.tenants where id = $1", tenant_id))


async def test_tenant_vencido_queda_purged_y_los_demas_intactos(entorno, db_superuser, escenario):
    _, auth, reportador, checkins, conn = entorno
    assert await conn.fetchval("select session_user") == "purge_job"

    codigo = await _correr_job(entorno)

    assert codigo == 0, reportador.excepciones
    v = await _tenant(db_superuser, escenario["v"])
    assert v["status"] == "purged" and v["slug"] != escenario["slug_v"] and "-purged-" in v["slug"]
    # slug liberado: otro negocio puede darse de alta con el original
    await db_superuser.execute(
        "insert into public.tenants (id, name, slug) values ($1, 'Nuevo', $2)", escenario["nuevo"], escenario["slug_v"]
    )
    assert (await _tenant(db_superuser, escenario["a"]))["status"] == "active"
    assert (await _tenant(db_superuser, escenario["r"]))["status"] == "inactive"
    # solo se pidió borrar la cuenta del tenant purgado; las otras dos siguen en auth.users
    assert auth.pedidos == [escenario["cuentas"]["v"]]
    restantes = await db_superuser.fetchval(
        "select count(*) from auth.users where id = any($1::uuid[])", list(escenario["cuentas"].values())
    )
    assert restantes == 2
    assert checkins.estados == ["in_progress", "ok"]


async def test_sin_google_configurado_el_paso_0_registra_el_fallo_y_la_purga_sigue(entorno, db_superuser, escenario):
    codigo = await _correr_job(entorno)

    assert codigo == 0
    fallos = await db_superuser.fetch(
        "select actor_type, metadata from public.audit_log where tenant_id = $1 and action = 'calendar_scrub_failed'",
        escenario["v"],
    )
    assert [f["actor_type"] for f in fallos] == ["system"]
    assert escenario["evento"] in fallos[0]["metadata"] and "calendar_no_configurado" in fallos[0]["metadata"]
    assert (await _tenant(db_superuser, escenario["v"]))["status"] == "purged"


async def test_exit_code_distinto_de_cero_si_algun_tenant_fallo(entorno, db_superuser, escenario):
    _, auth, reportador, checkins, _ = entorno
    auth.falla = True

    codigo = await _correr_job(entorno)

    assert codigo != 0
    assert checkins.estados == ["in_progress", "error"]
    pasos = [c["paso"] for _, c in reportador.excepciones if c.get("tenant_id") == str(escenario["v"])]
    assert pasos == ["delete_auth_accounts"]
    # la purga ya empezó (purging): no se puede reactivar y el próximo reintento la retoma
    assert (await _tenant(db_superuser, escenario["v"]))["status"] == "purging"
    assert (await _tenant(db_superuser, escenario["a"]))["status"] == "active"


async def test_la_corrida_es_reintentable_y_no_repite_eliminaciones(entorno, db_superuser, escenario):
    _, auth, _, _, _ = entorno

    assert await _correr_job(entorno) == 0
    despues_de_la_primera = await _tenant(db_superuser, escenario["v"])
    pedidos_primera = list(auth.pedidos)

    assert await _correr_job(entorno) == 0  # segunda corrida seguida: no falla
    assert auth.pedidos == pedidos_primera  # ni vuelve a eliminar cuentas
    assert await _tenant(db_superuser, escenario["v"]) == despues_de_la_primera


async def test_tras_un_fallo_la_corrida_siguiente_termina_la_purga(entorno, db_superuser, escenario):
    _, auth, _, _, _ = entorno
    auth.falla = True
    assert await _correr_job(entorno) != 0

    auth.falla = False
    assert await _correr_job(entorno) == 0
    assert (await _tenant(db_superuser, escenario["v"]))["status"] == "purged"
