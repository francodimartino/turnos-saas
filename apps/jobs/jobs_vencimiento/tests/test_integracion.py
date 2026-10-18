"""Job de vencimiento contra Postgres real, conectado como `scheduler_job`, con el HTTP de Telegram simulado."""

import asyncio
import json
import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import datetime

import httpx
import pytest
from platform_secrets import crear_almacen

from jobs_vencimiento import ejecutar

pytestmark = pytest.mark.integracion

asyncpg = pytest.importorskip("asyncpg")

TOKEN = f"{secrets.randbelow(10**9)}:{secrets.token_urlsafe(24)}"  # generado por corrida: sin credencial literal


class PoolDeUnaConexion:
    """Pool de tamaño 1 sobre la conexión del rol `scheduler_job`: los avisos corren en paralelo y se turnan."""

    def __init__(self, conn):
        self._conn = conn
        self._turno = asyncio.Lock()

    @asynccontextmanager
    async def acquire(self):
        async with self._turno:
            yield self._conn


async def _sin_espera(_):
    return None


def _http(enviados, rechazar=()):
    def handler(req):
        cuerpo = json.loads(req.content)
        if cuerpo["chat_id"] in rechazar:
            return httpx.Response(400, json={"ok": False, "error_code": 400, "description": "chat not found"})
        enviados.append(cuerpo)
        return httpx.Response(200, json={"ok": True})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class Reportes:
    def __init__(self):
        self.items = []

    def __call__(self, descripcion, **etiquetas):
        self.items.append((descripcion, etiquetas))


@pytest.fixture
async def mundo(db_superuser, conectar_como):
    """Fábrica de tenants con un turno `pending` vencido, un cliente y (opcional) bot de Telegram."""
    tenants: list[uuid.UUID] = []

    async def crear(chat: str | None, *, bot=True, estado="active") -> dict:
        d = {k: uuid.uuid4() for k in ("tenant", "prof", "servicio", "cliente", "booking")}
        tenants.append(d["tenant"])
        await db_superuser.execute(
            "insert into public.tenants (id, name, slug) values ($1, 'V', $2)",
            d["tenant"],
            f"it-venc-{uuid.uuid4().hex[:10]}",
        )
        await db_superuser.execute(
            "insert into public.professionals (id, tenant_id, name) values ($1, $2, 'P')", d["prof"], d["tenant"]
        )
        await db_superuser.execute(
            "insert into public.services (id, tenant_id, name, duration_minutes, price) values ($1, $2, 'S', 30, 10)",
            d["servicio"],
            d["tenant"],
        )
        canal = "telegram" if chat else None
        await db_superuser.execute(
            "insert into public.end_customers (id, tenant_id, channel, channel_user_id) values ($1, $2, $3, $4)",
            d["cliente"],
            d["tenant"],
            canal,
            chat,
        )
        await db_superuser.execute(
            "insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, "
            "status, pending_expires_at) values ($1, $2, $3, $4, $5, "
            "tstzrange($6::timestamptz, $6::timestamptz + interval '30 minutes'), "
            "'pending', now() - interval '5 minutes')",
            d["booking"],
            d["tenant"],
            d["cliente"],
            d["servicio"],
            d["prof"],
            datetime.fromisoformat("2031-03-01T10:00:00+00:00"),
        )
        if bot:
            admin = await conectar_como("platform_admin")
            await crear_almacen(admin).guardar(
                d["tenant"], "telegram_bot", {"webhook_key": f"it-{uuid.uuid4()}"}, json.dumps({"bot_token": TOKEN})
            )
        if estado != "active":
            await db_superuser.execute("update public.tenants set status = $2 where id = $1", d["tenant"], estado)
        return d

    try:
        yield crear
    finally:
        if tenants:
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
                for tabla in (
                    "notification_outbox",
                    "notifications_log",
                    "bookings",
                    "professionals",
                    "services",
                    "end_customers",
                ):
                    await db_superuser.execute(f"delete from public.{tabla} where tenant_id = any($1::uuid[])", tenants)
                await db_superuser.execute("delete from public.tenants where id = any($1::uuid[])", tenants)


async def _estado(db, booking):
    return await db.fetchval("select status from public.bookings where id = $1", booking)


async def test_vencido_se_cancela_y_el_cliente_es_avisado_y_es_idempotente(conectar_como, db_superuser, mundo):
    d = await mundo("chat-it-1")
    pool = PoolDeUnaConexion(await conectar_como("scheduler_job"))
    enviados, reportes = [], Reportes()
    r = await ejecutar(pool, cliente_http=_http(enviados), esperar=_sin_espera, reportar=reportes)
    assert await _estado(db_superuser, d["booking"]) == "cancelled"
    assert [e["chat_id"] for e in enviados] == ["chat-it-1"]
    assert r.avisados == 1 and r.fallos == 0 and r.exit_code == 0 and reportes.items == []

    enviados.clear()
    r2 = await ejecutar(pool, cliente_http=_http(enviados), esperar=_sin_espera, reportar=reportes)
    assert (r2.vencidos, r2.avisados, r2.exit_code) == (0, 0, 0)
    assert enviados == []


async def test_tenant_suspendido_o_cliente_sin_canal_se_cancelan_sin_aviso(conectar_como, db_superuser, mundo):
    suspendido = await mundo("chat-it-susp", estado="suspended")
    sin_canal = await mundo(None)
    pool = PoolDeUnaConexion(await conectar_como("scheduler_job"))
    enviados = []
    r = await ejecutar(pool, cliente_http=_http(enviados), esperar=_sin_espera, reportar=Reportes())
    assert await _estado(db_superuser, suspendido["booking"]) == "cancelled"
    assert await _estado(db_superuser, sin_canal["booking"]) == "cancelled"
    assert enviados == [] and r.avisados == 0 and r.sin_aviso >= 2 and r.exit_code == 0


async def test_si_falla_un_aviso_los_demas_se_procesan_y_se_reporta(conectar_como, db_superuser, mundo):
    malo = await mundo("chat-it-malo")
    sin_bot = await mundo("chat-it-sinbot", bot=False)
    bueno = await mundo("chat-it-bueno")
    pool = PoolDeUnaConexion(await conectar_como("scheduler_job"))
    enviados, reportes = [], Reportes()
    r = await ejecutar(
        pool, cliente_http=_http(enviados, rechazar={"chat-it-malo"}), esperar=_sin_espera, reportar=reportes
    )
    assert [e["chat_id"] for e in enviados] == ["chat-it-bueno"]
    assert r.avisados == 1 and r.fallos == 2 and r.exit_code == 1
    assert {e["booking_id"] for _, e in reportes.items} == {malo["booking"], sin_bot["booking"]}
    for d in (malo, sin_bot, bueno):  # todos quedaron cancelados igual
        assert await _estado(db_superuser, d["booking"]) == "cancelled"
    volcado = json.dumps([(x, {k: str(v) for k, v in e.items()}) for x, e in reportes.items])
    assert "chat-it-" not in volcado and TOKEN not in volcado


async def test_poda_sesiones_sin_actividad(conectar_como, db_superuser, mundo):
    d = await mundo(None, bot=False)
    for usuario, minutos in (("viejo", 120), ("reciente", 5)):
        await db_superuser.execute(
            "insert into public.bot_sessions (tenant_id, channel, channel_user_id, updated_at) "
            "values ($1, 'telegram', $2, now() - ($3::text || ' minutes')::interval)",
            d["tenant"],
            usuario,
            str(minutos),
        )
    pool = PoolDeUnaConexion(await conectar_como("scheduler_job"))
    r = await ejecutar(pool, cliente_http=_http([]), esperar=_sin_espera, reportar=Reportes())
    assert r.sesiones_borradas >= 1
    restantes = await db_superuser.fetch(
        "select channel_user_id from public.bot_sessions where tenant_id = $1", d["tenant"]
    )
    await db_superuser.execute("delete from public.bot_sessions where tenant_id = $1", d["tenant"])
    assert [x["channel_user_id"] for x in restantes] == ["reciente"]


async def _log(db, cliente):
    return await db.fetch(
        "select status, retry_count, channel, template, last_error from public.notifications_log "
        "where end_customer_id = $1",
        cliente,
    )


async def test_aviso_exitoso_queda_registrado_como_sent(conectar_como, db_superuser, mundo):
    d = await mundo("chat-it-log-ok")
    pool = PoolDeUnaConexion(await conectar_como("scheduler_job"))
    await ejecutar(pool, cliente_http=_http([]), backoff=(0, 0, 0), esperar=_sin_espera, reportar=Reportes())
    filas = await _log(db_superuser, d["cliente"])
    assert len(filas) == 1
    assert (filas[0]["status"], filas[0]["channel"], filas[0]["template"]) == ("sent", "telegram", "turno_vencido")


async def test_aviso_rechazado_queda_registrado_como_failed_sin_pii(conectar_como, db_superuser, mundo):
    d = await mundo("chat-it-log-mal")
    pool = PoolDeUnaConexion(await conectar_como("scheduler_job"))
    r = await ejecutar(
        pool,
        cliente_http=_http([], rechazar={"chat-it-log-mal"}),
        backoff=(0, 0, 0),
        esperar=_sin_espera,
        reportar=Reportes(),
    )
    filas = await _log(db_superuser, d["cliente"])
    assert r.fallos == 1
    assert (
        len(filas) == 1 and filas[0]["status"] == "failed" and filas[0]["retry_count"] == 0
    )  # 400: permanente, sin reintentos
    assert "chat-it-log-mal" not in (filas[0]["last_error"] or "") and TOKEN not in (filas[0]["last_error"] or "")


# --- Reescaneo del outbox (§6.4) -------------------------------------------------------------------------------------


async def _vencer_sin_avisar(conectar_como):
    """Lo que hace el job antes de morir: vence los turnos (y encola sus avisos) y no manda nada."""
    conn = await conectar_como("scheduler_job")
    return await conn.fetch("select * from public.expire_pending_bookings()")


async def _fila_outbox(db, cliente):
    return await db.fetchrow(
        "select id, status, attempts, last_error from public.notification_outbox where end_customer_id = $1", cliente
    )


async def _hacer_vencer_la_espera(db, cliente):
    await db.execute(
        "update public.notification_outbox set next_attempt_at = now() - interval '1 minute' "
        "where end_customer_id = $1 and status = 'pending'",
        cliente,
    )


async def test_el_vencimiento_encola_el_aviso_y_el_envio_inmediato_cierra_la_fila(conectar_como, db_superuser, mundo):
    d = await mundo("chat-it-ob1")
    pool = PoolDeUnaConexion(await conectar_como("scheduler_job"))
    enviados = []
    await ejecutar(pool, cliente_http=_http(enviados), esperar=_sin_espera, reportar=Reportes())
    fila = await _fila_outbox(db_superuser, d["cliente"])
    assert fila["status"] == "sent" and [e["chat_id"] for e in enviados] == ["chat-it-ob1"]


async def test_una_fila_abandonada_se_envia_en_la_corrida_siguiente_y_termina_sent(conectar_como, db_superuser, mundo):
    d = await mundo("chat-it-ob2")
    await _vencer_sin_avisar(conectar_como)  # el job murió después de vencer, antes de avisar
    assert (await _fila_outbox(db_superuser, d["cliente"]))["status"] == "pending"
    pool = PoolDeUnaConexion(await conectar_como("scheduler_job"))

    enviados = []
    await ejecutar(pool, cliente_http=_http(enviados), esperar=_sin_espera, reportar=Reportes())
    assert enviados == []  # todavía dentro de la ventana del envío inmediato: no se pisa

    await _hacer_vencer_la_espera(db_superuser, d["cliente"])
    r = await ejecutar(pool, cliente_http=_http(enviados), esperar=_sin_espera, reportar=Reportes())
    assert [e["chat_id"] for e in enviados] == ["chat-it-ob2"]
    assert "venció" in enviados[0]["text"]
    assert (await _fila_outbox(db_superuser, d["cliente"]))["status"] == "sent"
    assert r.reescaneo_enviadas >= 1 and r.exit_code == 0

    enviados.clear()  # una fila cerrada no se reprocesa
    await ejecutar(pool, cliente_http=_http(enviados), esperar=_sin_espera, reportar=Reportes())
    assert enviados == []


async def test_si_el_job_muere_a_mitad_del_lote_los_turnos_vencidos_igual_reciben_su_aviso(
    conectar_como, db_superuser, mundo
):
    d = await mundo("chat-it-ob3")
    colgado = []

    async def handler(req):
        colgado.append(1)
        await asyncio.sleep(30)  # el envío no termina dentro del plazo de la corrida
        return httpx.Response(200, json={"ok": True})

    pool = PoolDeUnaConexion(await conectar_como("scheduler_job"))
    primera = await ejecutar(
        pool,
        cliente_http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        esperar=_sin_espera,
        reportar=Reportes(),
        plazo_total=0.2,
    )
    assert colgado and primera.exit_code == 1
    assert await _estado(db_superuser, d["booking"]) == "cancelled"
    assert (await _fila_outbox(db_superuser, d["cliente"]))["status"] == "pending"

    await _hacer_vencer_la_espera(db_superuser, d["cliente"])
    enviados = []
    segunda = await ejecutar(pool, cliente_http=_http(enviados), esperar=_sin_espera, reportar=Reportes())
    assert [e["chat_id"] for e in enviados] == ["chat-it-ob3"] and segunda.exit_code == 0
    assert (await _fila_outbox(db_superuser, d["cliente"]))["status"] == "sent"


async def test_una_fila_que_agota_sus_intentos_queda_failed_y_no_se_reprocesa(conectar_como, db_superuser, mundo):
    d = await mundo("chat-it-ob4")
    await _vencer_sin_avisar(conectar_como)
    await db_superuser.execute(
        "update public.notification_outbox set attempts = 5, next_attempt_at = now() - interval '1 minute' "
        "where end_customer_id = $1",
        d["cliente"],
    )
    pool = PoolDeUnaConexion(await conectar_como("scheduler_job"))
    enviados = []
    await ejecutar(pool, cliente_http=_http(enviados), esperar=_sin_espera, reportar=Reportes())
    fila = await _fila_outbox(db_superuser, d["cliente"])
    assert fila["status"] == "failed" and enviados == []

    await ejecutar(pool, cliente_http=_http(enviados), esperar=_sin_espera, reportar=Reportes())
    assert enviados == [] and (await _fila_outbox(db_superuser, d["cliente"]))["status"] == "failed"


async def test_un_error_transitorio_se_reintenta_y_al_ultimo_intento_queda_failed(conectar_como, db_superuser, mundo):
    d = await mundo("chat-it-ob5")
    await _vencer_sin_avisar(conectar_como)
    pool = PoolDeUnaConexion(await conectar_como("scheduler_job"))

    def caido(req):
        return httpx.Response(500, json={"ok": False, "error_code": 500, "description": "boom"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(caido))
    for esperado, intento in (("pending", 1), ("pending", 2), ("failed", 3)):
        await _hacer_vencer_la_espera(db_superuser, d["cliente"])
        await ejecutar(
            pool, cliente_http=http, esperar=_sin_espera, reportar=Reportes(), max_intentos_outbox=3, arriendo_outbox=30
        )
        fila = await _fila_outbox(db_superuser, d["cliente"])
        assert (fila["status"], fila["attempts"]) == (esperado, intento)


async def test_dos_corridas_simultaneas_no_envian_dos_veces_la_misma_fila(conectar_como, db_superuser, mundo):
    d = await mundo("chat-it-ob6")
    await _vencer_sin_avisar(conectar_como)
    await _hacer_vencer_la_espera(db_superuser, d["cliente"])
    pool_a = PoolDeUnaConexion(await conectar_como("scheduler_job"))
    pool_b = PoolDeUnaConexion(await conectar_como("scheduler_job"))
    enviados = []
    await asyncio.gather(
        ejecutar(pool_a, cliente_http=_http(enviados), esperar=_sin_espera, reportar=Reportes()),
        ejecutar(pool_b, cliente_http=_http(enviados), esperar=_sin_espera, reportar=Reportes()),
    )
    assert [e["chat_id"] for e in enviados] == ["chat-it-ob6"]
    fila = await _fila_outbox(db_superuser, d["cliente"])
    assert fila["status"] == "sent" and fila["attempts"] == 1
