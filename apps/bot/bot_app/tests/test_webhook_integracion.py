"""Webhook de Telegram contra Postgres real, con el pool conectado como `bot_service` (Telegram simulado).

El tenant, su integración y el secret se dan de alta como `platform_admin` (así se hace en producción);
el bot solo lee/escribe con sus propios grants y la RLS.
"""

import json
import secrets
import uuid
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
import pytest
from platform_secrets import crear_almacen
from platform_tenancy import crear_pool

from bot_app import Despachador, crear_app
from bot_app.config import verificar_rol
from bot_app.tests.conftest import update

pytestmark = pytest.mark.integracion

asyncpg = pytest.importorskip("asyncpg")

CABECERA = "X-Telegram-Bot-Api-Secret-Token"


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
async def pool_bot(database_url, roles_password):
    pool = await crear_pool(
        _dsn(database_url, "bot_service", roles_password), min_size=1, max_size=3, statement_cache_size=0
    )
    try:
        yield pool
    finally:
        await pool.close()


class Escenario:
    def __init__(self, tenant_id, clave, token, webhook_secret) -> None:
        self.tenant_id, self.clave, self.token, self.webhook_secret = tenant_id, clave, token, webhook_secret

    @property
    def ruta(self) -> str:
        return f"/api/bot/telegram/{self.clave}"


async def _alta(db_superuser, conectar_como, status: str = "active") -> Escenario:
    tenant = uuid.uuid4()
    clave = str(uuid.uuid4())
    token = f"{secrets.randbelow(10**9)}:{secrets.token_urlsafe(24)}"
    webhook_secret = secrets.token_urlsafe(24)
    await db_superuser.execute(
        "insert into public.tenants (id, name, slug) values ($1, 'N', $2)", tenant, f"it-bot-{uuid.uuid4().hex[:8]}"
    )
    admin = await conectar_como("platform_admin")
    secreto = json.dumps({"bot_token": token, "webhook_secret": webhook_secret})
    await crear_almacen(admin).guardar(tenant, "telegram_bot", {"webhook_key": clave}, secreto)
    if status != "active":
        await db_superuser.execute("update public.tenants set status = $2 where id = $1", tenant, status)
    return Escenario(tenant, clave, token, webhook_secret)


@pytest.fixture
async def escenarios(db_superuser, conectar_como):
    creados: list[Escenario] = []

    async def crear(status: str = "active") -> Escenario:
        e = await _alta(db_superuser, conectar_como, status)
        creados.append(e)
        return e

    try:
        yield crear
    finally:
        tenants = [e.tenant_id for e in creados]
        async with db_superuser.transaction():
            await db_superuser.execute("delete from public.inbound_messages where tenant_id = any($1::uuid[])", tenants)
            await db_superuser.execute("delete from public.bot_sessions where tenant_id = any($1::uuid[])", tenants)
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


class Telegram:
    def __init__(self) -> None:
        self.llamadas: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.llamadas.append({"url": str(request.url), "cuerpo": json.loads(request.content)})
        return httpx.Response(200, json={"ok": True})


def montar(pool, handlers=()):
    telegram = Telegram()
    app = crear_app(
        pool=pool,
        despachador=Despachador(handlers),
        cliente_http=httpx.AsyncClient(transport=httpx.MockTransport(telegram.handler)),
    )
    transporte = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transporte, base_url="http://bot"), telegram


async def _marcas(db, tenant) -> int:
    return await db.fetchval("select count(*) from public.inbound_messages where tenant_id = $1", tenant)


async def _sesiones(db, tenant) -> int:
    return await db.fetchval("select count(*) from public.bot_sessions where tenant_id = $1", tenant)


async def test_tenant_suspendido_responde_200_sin_procesar_ni_escribir(pool_bot, db_superuser, escenarios):
    e = await escenarios("suspended")
    llamados = []

    async def handler(ctx):
        llamados.append(1)
        return True

    cliente, telegram = montar(pool_bot, [handler])
    r = await cliente.post(e.ruta, json=update(), headers={CABECERA: e.webhook_secret})
    assert r.status_code == 200
    assert llamados == [] and telegram.llamadas == []
    assert await _marcas(db_superuser, e.tenant_id) == 0 and await _sesiones(db_superuser, e.tenant_id) == 0


async def test_clave_desconocida_responde_200_sin_procesar(pool_bot):
    llamados = []

    async def handler(ctx):
        llamados.append(1)
        return True

    cliente, _ = montar(pool_bot, [handler])
    r = await cliente.post(f"/api/bot/telegram/{uuid.uuid4()}", json=update(), headers={CABECERA: "x"})
    assert r.status_code == 200 and llamados == []


@pytest.mark.parametrize("variante", ["incorrecto", "ausente"])
async def test_secret_erroneo_da_401_sin_dejar_marca_ni_sesion(pool_bot, db_superuser, escenarios, variante):
    e = await escenarios()
    llamados = []

    async def handler(ctx):
        llamados.append(1)
        ctx.sesion.identidad = {"paso": "telefono"}
        return True

    cliente, telegram = montar(pool_bot, [handler])
    cabeceras = {CABECERA: "no-es-el-secret"} if variante == "incorrecto" else {}
    r = await cliente.post(e.ruta, json=update(), headers=cabeceras)
    assert r.status_code == 401
    assert llamados == [] and telegram.llamadas == []
    assert await _marcas(db_superuser, e.tenant_id) == 0 and await _sesiones(db_superuser, e.tenant_id) == 0


async def test_el_secret_de_otro_tenant_no_vale(pool_bot, db_superuser, escenarios):
    a, b = await escenarios(), await escenarios()
    cliente, _ = montar(pool_bot)
    r = await cliente.post(a.ruta, json=update(), headers={CABECERA: b.webhook_secret})
    assert r.status_code == 401
    assert await _marcas(db_superuser, a.tenant_id) == 0


async def test_update_repetido_se_procesa_una_sola_vez(pool_bot, db_superuser, escenarios):
    e = await escenarios()
    llamados = []

    async def handler(ctx):
        llamados.append(ctx.mensaje.update_id)
        ctx.responder("hola")
        return True

    cliente, telegram = montar(pool_bot, [handler])
    for _ in range(3):
        r = await cliente.post(e.ruta, json=update(77), headers={CABECERA: e.webhook_secret})
        assert r.status_code == 200
    assert llamados == ["77"]
    assert len(telegram.llamadas) == 1
    assert telegram.llamadas[0]["url"].startswith("https://api.telegram.org/bot")
    assert await _marcas(db_superuser, e.tenant_id) == 1


async def test_excepcion_al_procesar_revierte_la_marca_y_la_reentrega_se_reprocesa(pool_bot, db_superuser, escenarios):
    e = await escenarios()
    estado = {"falla": True}
    llamados = []

    async def handler(ctx):
        llamados.append(1)
        ctx.sesion.identidad = {"paso": "telefono"}
        if estado["falla"]:
            raise RuntimeError("falla simulada")
        ctx.responder("ok")
        return True

    cliente, telegram = montar(pool_bot, [handler])
    r1 = await cliente.post(e.ruta, json=update(5), headers={CABECERA: e.webhook_secret})
    assert r1.status_code == 500
    assert await _marcas(db_superuser, e.tenant_id) == 0 and await _sesiones(db_superuser, e.tenant_id) == 0
    assert telegram.llamadas == []
    estado["falla"] = False
    r2 = await cliente.post(e.ruta, json=update(5), headers={CABECERA: e.webhook_secret})
    assert r2.status_code == 200
    assert len(llamados) == 2 and len(telegram.llamadas) == 1
    assert await _marcas(db_superuser, e.tenant_id) == 1


async def test_sesion_persistida_y_descartada_tras_una_hora_sin_actividad(pool_bot, db_superuser, escenarios):
    e = await escenarios()
    vistas = []

    async def handler(ctx):
        vistas.append(ctx.sesion.a_json())
        ctx.sesion.identidad = {"paso": "telefono", "vez": str(int((ctx.sesion.identidad or {}).get("vez", "0")) + 1)}
        return True

    cliente, _ = montar(pool_bot, [handler])
    cab = {CABECERA: e.webhook_secret}
    await cliente.post(e.ruta, json=update(1, usuario=4242), headers=cab)
    await cliente.post(e.ruta, json=update(2, usuario=4242), headers=cab)
    assert vistas == [{"version": 1}, {"version": 1, "identidad": {"paso": "telefono", "vez": "1"}}]
    fila = await db_superuser.fetchrow(
        "select channel, state, updated_at from public.bot_sessions where tenant_id = $1 and channel_user_id = '4242'",
        e.tenant_id,
    )
    assert fila["channel"] == "telegram" and json.loads(fila["state"]) == {
        "version": 1,
        "identidad": {"paso": "telefono", "vez": "2"},
    }

    # Más de 1 hora sin actividad: al leerla se descarta y el flujo arranca de cero.
    await db_superuser.execute(
        "update public.bot_sessions set updated_at = now() - interval '61 minutes' where tenant_id = $1", e.tenant_id
    )
    await cliente.post(e.ruta, json=update(3, usuario=4242), headers=cab)
    assert vistas[2] == {"version": 1}
    assert await _sesiones(db_superuser, e.tenant_id) == 1  # la nueva, con estado fresco


async def test_la_actividad_renueva_la_sesion(pool_bot, db_superuser, escenarios):
    e = await escenarios()

    async def handler(ctx):
        ctx.sesion.identidad = {"paso": "telefono"}
        return True

    cliente, _ = montar(pool_bot, [handler])
    cab = {CABECERA: e.webhook_secret}
    await cliente.post(e.ruta, json=update(1, usuario=1), headers=cab)
    await db_superuser.execute(
        "update public.bot_sessions set updated_at = now() - interval '50 minutes' where tenant_id = $1", e.tenant_id
    )
    await cliente.post(e.ruta, json=update(2, usuario=1), headers=cab)
    minutos = await db_superuser.fetchval(
        "select extract(epoch from now() - updated_at) / 60 from public.bot_sessions where tenant_id = $1", e.tenant_id
    )
    assert minutos < 5


async def test_las_sesiones_son_por_tenant_y_por_usuario(pool_bot, db_superuser, escenarios):
    a, b = await escenarios(), await escenarios()
    vistas = []

    async def handler(ctx):
        vistas.append((str(ctx.tenant_id), ctx.mensaje.usuario_id, ctx.sesion.a_json()))
        ctx.sesion.identidad = {"paso": "telefono", "marca": str(ctx.tenant_id)}
        return True

    cliente, _ = montar(pool_bot, [handler])
    await cliente.post(a.ruta, json=update(1, usuario=9), headers={CABECERA: a.webhook_secret})
    await cliente.post(b.ruta, json=update(1, usuario=9), headers={CABECERA: b.webhook_secret})
    assert vistas[1][2] == {"version": 1}  # mismo usuario y mismo update_id, otro tenant: nada compartido
    assert await _sesiones(db_superuser, a.tenant_id) == 1 and await _sesiones(db_superuser, b.tenant_id) == 1


async def test_todas_las_queries_corren_como_bot_service(pool_bot, escenarios):
    e = await escenarios()
    roles = []

    async def handler(ctx):
        fila = await ctx.conn.fetchrow("select current_user::text as actual, session_user::text as sesion")
        roles.append((fila["actual"], fila["sesion"]))
        return True

    cliente, _ = montar(pool_bot, [handler])
    await cliente.post(e.ruta, json=update(), headers={CABECERA: e.webhook_secret})
    assert roles == [("bot_service", "bot_service")]
    await verificar_rol(pool_bot)


async def test_health_con_la_base_real(pool_bot):
    cliente, _ = montar(pool_bot)
    r = await cliente.get("/health")
    assert r.status_code == 200 and r.json() == {"estado": "ok"}
