"""El enviador contra Postgres real, conectado como el rol `enviador`, con Telegram y el email simulados.

Lo que acá se prueba y no se puede probar con una base falsa: el reclamo con SKIP LOCKED y arriendo (dos instancias,
y el job de vencimiento, no reciben la misma fila), lo que devuelve `resolve_outbox_delivery`, el registro por canal en
`entregas` y su CHECK de tamaño, el aislamiento entre tenants y que el rol `enviador` alcanza con sus grants exactos.
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
import pytest
from platform_notifications import BACKOFF_POR_DEFECTO
from platform_secrets import crear_almacen

from enviador_app.bucle import Enviador
from enviador_app.procesador import MAX_DESTINATARIOS_POR_FILA, ProcesadorOutbox
from enviador_app.tests.soporte import CorreoSimulado, Reportes, error_email, sin_espera

pytestmark = pytest.mark.integracion

asyncpg = pytest.importorskip("asyncpg")

INICIO = datetime.fromisoformat("2031-03-01T10:00:00+00:00")


class PoolDeUnaConexion:
    """Pool de tamaño 1 sobre la conexión del rol `enviador`: las filas corren en paralelo y se turnan."""

    def __init__(self, conn):
        self._conn = conn
        self._turno = asyncio.Lock()

    @asynccontextmanager
    async def acquire(self):
        async with self._turno:
            yield self._conn


class BotApi:
    """Bot API simulada: cada pedido guarda el token (va en la URL) y el chat; los `rechazar` dan un 5xx."""

    def __init__(self, fallar_con=None):
        self.pedidos = []
        self.fallar_con = fallar_con  # estado HTTP para todos los pedidos, o None
        self.cliente = httpx.AsyncClient(transport=httpx.MockTransport(self._responder))

    def _responder(self, req):
        cuerpo = json.loads(req.content)
        token = str(req.url).split("/bot", 1)[1].split("/", 1)[0]
        self.pedidos.append((token, cuerpo["chat_id"]))
        if self.fallar_con is not None:
            return httpx.Response(self.fallar_con, json={"ok": False, "error_code": self.fallar_con})
        return httpx.Response(200, json={"ok": True})


def _enviador(conn, bot, correo, *, lote=50, max_intentos=5):
    pool = PoolDeUnaConexion(conn)
    proc = ProcesadorOutbox(
        pool,
        cliente_http=bot.cliente,
        cliente_email=correo,
        base_url_telegram="https://api.telegram.org",
        backoff=BACKOFF_POR_DEFECTO,
        esperar=sin_espera,
        max_intentos=max_intentos,
        reintento_seg=300,
        reportar=Reportes(),
    )
    return Enviador(
        pool,
        proc,
        lote=lote,
        concurrencia=5,
        arriendo_seg=300,
        max_intentos=max_intentos,
        plazo_fila_seg=30.0,
        plazo_apagado_seg=2.0,
        intervalo_seg=1.0,
        espera_maxima_seg=2.0,
        reportar=Reportes(),
    )


@pytest.fixture
async def negocios(db_superuser, conectar_como):
    """Fábrica de negocios con un turno, un cliente con Telegram y email verificados y el bot del negocio."""
    tenants: list[uuid.UUID] = []

    async def crear(*, bot=True, estado="active") -> dict:
        d: dict[str, Any] = {k: uuid.uuid4() for k in ("tenant", "prof", "servicio", "cliente", "booking")}
        sufijo = uuid.uuid4().hex[:10]
        d.update(
            chat=f"chat-{sufijo}", correo=f"cliente-{sufijo}@ejemplo.test", token=f"{_numero_al_azar()}:tok-{sufijo}"
        )
        tenants.append(d["tenant"])
        await db_superuser.execute(
            "insert into public.tenants (id, name, slug) values ($1, 'E', $2)", d["tenant"], f"it-env-{sufijo}"
        )
        await db_superuser.execute(
            "insert into public.professionals (id, tenant_id, name) values ($1, $2, 'P')", d["prof"], d["tenant"]
        )
        await db_superuser.execute(
            "insert into public.services (id, tenant_id, name, duration_minutes, price) values ($1, $2, 'S', 30, 10)",
            d["servicio"],
            d["tenant"],
        )
        await db_superuser.execute(
            "insert into public.end_customers (id, tenant_id, channel, channel_user_id) "
            "values ($1, $2, 'telegram', $3)",
            d["cliente"],
            d["tenant"],
            d["chat"],
        )
        for canal, identificador in (("telegram", d["chat"]), ("email", d["correo"])):
            await db_superuser.execute(
                "insert into public.canales_cliente (tenant_id, end_customer_id, canal, identificador, verificado_at) "
                "values ($1, $2, $3, $4, now()) on conflict do nothing",
                d["tenant"],
                d["cliente"],
                canal,
                identificador,
            )
        await db_superuser.execute(
            "insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, "
            "status) values ($1, $2, $3, $4, $5, tstzrange($6::timestamptz, $6::timestamptz + interval '30 minutes'), "
            "'confirmed')",
            d["booking"],
            d["tenant"],
            d["cliente"],
            d["servicio"],
            d["prof"],
            INICIO,
        )
        if bot:
            admin = await conectar_como("platform_admin")
            await crear_almacen(admin).guardar(
                d["tenant"],
                "telegram_bot",
                {"webhook_key": f"it-{uuid.uuid4()}"},
                json.dumps({"bot_token": d["token"]}),
            )
        if estado != "active":
            await db_superuser.execute("update public.tenants set status = $2 where id = $1", d["tenant"], estado)
        return d

    try:
        yield crear
    finally:
        if tenants:
            async with db_superuser.transaction():
                await db_superuser.execute("set local session_replication_role = replica")
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
                    "avisos_negocio_config",
                    "tenant_users",
                    "notification_outbox",
                    "notifications_log",
                    "bookings",
                    "canales_cliente",
                    "end_customers",
                    "services",
                    "professionals",
                ):
                    await db_superuser.execute(f"delete from public.{tabla} where tenant_id = any($1::uuid[])", tenants)
                await db_superuser.execute("delete from public.tenants where id = any($1::uuid[])", tenants)


def _numero_al_azar() -> int:
    return uuid.uuid4().int % 10**9


async def _encolar(db, d, template="turno_confirmado", *, demora="1 minute"):
    """Una fila `pending` ya vencida para el cliente del negocio `d`."""
    return await db.fetchval(
        "insert into public.notification_outbox "
        "(tenant_id, end_customer_id, channel, template, params, next_attempt_at) "
        "values ($1, $2, 'telegram', $3, jsonb_build_object('booking_id', $4::text, 'inicio', $5::text), "
        "now() - $6::interval) returning id",
        d["tenant"],
        d["cliente"],
        template,
        str(d["booking"]),
        INICIO.isoformat(),
        demora,
    )


async def _fila(db, id_):
    return await db.fetchrow(
        "select status, attempts, last_error, entregas::text as entregas, next_attempt_at > now() as en_el_futuro "
        "from public.notification_outbox where id = $1",
        id_,
    )


async def test_envia_a_los_dos_negocios_con_su_propio_token_y_registra_cada_uno_en_lo_suyo(
    conectar_como, db_superuser, negocios
):
    a, b = await negocios(), await negocios()
    ids = {d["tenant"]: await _encolar(db_superuser, d) for d in (a, b)}
    bot, correo = BotApi(), CorreoSimulado()
    env = _enviador(await conectar_como("enviador"), bot, correo)
    await env.ciclo(asyncio.Event())

    assert sorted(bot.pedidos) == sorted([(a["token"], a["chat"]), (b["token"], b["chat"])])  # cada uno, con su token
    assert sorted(d for d, _, _ in correo.enviados) == sorted([a["correo"], b["correo"]])
    for d in (a, b):
        fila = await _fila(db_superuser, ids[d["tenant"]])
        assert fila["status"] == "sent" and fila["attempts"] == 1
        assert fila["entregas"].count('"sent"') == 2
        assert d["chat"] not in fila["entregas"] and d["correo"] not in fila["entregas"]  # claves, nunca el destino
    # `notifications_log`: dos registros por negocio (telegram y email), cada uno en su tenant y de su cliente
    registros = await db_superuser.fetch(
        "select tenant_id, end_customer_id, channel, status from public.notifications_log "
        "where tenant_id = any($1::uuid[])",
        [a["tenant"], b["tenant"]],
    )
    assert sorted((r["tenant_id"], r["channel"], r["status"]) for r in registros) == sorted(
        (d["tenant"], canal, "sent") for d in (a, b) for canal in ("telegram", "email")
    )
    assert {(r["tenant_id"], r["end_customer_id"]) for r in registros} == {
        (a["tenant"], a["cliente"]),
        (b["tenant"], b["cliente"]),
    }
    assert env.contadores.por_canal == {"telegram": {"sent": 2, "failed": 0}, "email": {"sent": 2, "failed": 0}}


async def test_dos_instancias_no_envian_la_misma_fila(conectar_como, db_superuser, negocios):
    d = await negocios()
    ids = {await _encolar(db_superuser, d) for _ in range(6)}
    bot, correo = BotApi(), CorreoSimulado()
    uno = _enviador(await conectar_como("enviador"), bot, correo, lote=3)
    otro = _enviador(await conectar_como("enviador"), bot, correo, lote=3)
    await asyncio.gather(uno.ciclo(asyncio.Event()), otro.ciclo(asyncio.Event()))

    assert uno.contadores.reclamadas + otro.contadores.reclamadas == 6  # lo reclamado por una no lo recibe la otra
    assert len(bot.pedidos) == 6 and len(correo.enviados) == 6  # una vez por fila y por canal
    estados = await db_superuser.fetch(
        "select status, attempts from public.notification_outbox where id = any($1)", list(ids)
    )
    assert {(f["status"], f["attempts"]) for f in estados} == {("sent", 1)}


async def test_el_job_de_vencimiento_y_el_enviador_no_se_reparten_la_misma_fila(conectar_como, db_superuser, negocios):
    """Mientras el job de vencimiento siga reescaneando, el arriendo decide quién envía: el que reclama primero."""
    d = await negocios()
    # un cliente con un único canal verificado (Telegram) es lo único que reclama el job
    await db_superuser.execute(
        "delete from public.canales_cliente where tenant_id = $1 and canal = 'email'", d["tenant"]
    )
    primera = await _encolar(db_superuser, d)
    job = await conectar_como("scheduler_job")
    bot, correo = BotApi(), CorreoSimulado()
    env = _enviador(await conectar_como("enviador"), bot, correo)

    reclamadas_por_el_job = await job.fetch("select id from public.reclamar_avisos_telegram(50, 300, 5)")
    assert primera in {f["id"] for f in reclamadas_por_el_job}
    await env.ciclo(asyncio.Event())
    assert bot.pedidos == [] and correo.enviados == []  # arrendada por el job: el enviador no la toca

    segunda = await _encolar(db_superuser, d)
    await env.ciclo(asyncio.Event())  # la reclama el enviador
    assert [t for t, _ in bot.pedidos] == [d["token"]] and (await _fila(db_superuser, segunda))["status"] == "sent"
    assert segunda not in {
        f["id"] for f in await job.fetch("select id from public.reclamar_avisos_telegram(50, 300, 5)")
    }


async def test_el_telegram_ya_registrado_no_se_repite_y_solo_sale_el_email(conectar_como, db_superuser, negocios):
    d = await negocios()
    id_ = await _encolar(db_superuser, d)
    clave = "cc:" + str(
        await db_superuser.fetchval(
            "select id from public.canales_cliente where tenant_id = $1 and canal = 'telegram'", d["tenant"]
        )
    )
    previo = await conectar_como("enviador")
    async with previo.transaction():
        await previo.execute("select set_config('app.current_tenant_id', $1, true)", str(d["tenant"]))
        assert await previo.fetchval("select public.registrar_entrega_outbox($1, $2, 'sent', null)", id_, clave)
    bot, correo = BotApi(), CorreoSimulado()
    await _enviador(await conectar_como("enviador"), bot, correo).ciclo(asyncio.Event())
    assert bot.pedidos == [] and [x for x, _, _ in correo.enviados] == [d["correo"]]
    assert (await _fila(db_superuser, id_))["status"] == "sent"


async def test_un_fallo_transitorio_reprograma_y_el_reintento_solo_repite_lo_que_no_salio(
    conectar_como, db_superuser, negocios
):
    d = await negocios()
    id_ = await _encolar(db_superuser, d)
    bot = BotApi(fallar_con=503)
    correo = CorreoSimulado()
    env = _enviador(await conectar_como("enviador"), bot, correo)
    await env.ciclo(asyncio.Event())
    fila = await _fila(db_superuser, id_)
    assert fila["status"] == "pending" and fila["attempts"] == 1 and fila["en_el_futuro"]
    assert fila["last_error"].startswith("telegram: ") and len(correo.enviados) == 1
    assert len(bot.pedidos) == 1 + len(BACKOFF_POR_DEFECTO)

    await db_superuser.execute("update public.notification_outbox set next_attempt_at = now() where id = $1", id_)
    bot.fallar_con = None
    await env.ciclo(asyncio.Event())
    fila = await _fila(db_superuser, id_)
    assert fila["status"] == "sent" and fila["attempts"] == 2
    assert len(correo.enviados) == 1  # el email que ya salió no se repite
    assert len(bot.pedidos) == 2 + len(BACKOFF_POR_DEFECTO)
    assert fila["entregas"].count('"sent"') == 2


async def test_sin_destinatarios_o_con_el_negocio_inactivo_la_fila_se_cierra_failed_sin_enviar(
    conectar_como, db_superuser, negocios
):
    suspendido = await negocios(estado="suspended")
    anonimizado = await negocios()
    await db_superuser.execute(
        "update public.end_customers set deleted_at = now() where id = $1", anonimizado["cliente"]
    )
    ids = {
        "suspendido": await _encolar(db_superuser, suspendido),
        "anonimizado": await _encolar(db_superuser, anonimizado),
    }
    bot, correo = BotApi(), CorreoSimulado()
    env = _enviador(await conectar_como("enviador"), bot, correo)
    await env.ciclo(asyncio.Event())
    assert bot.pedidos == [] and correo.enviados == []
    assert (await _fila(db_superuser, ids["suspendido"]))["last_error"] == "tenant_inactivo"
    assert (await _fila(db_superuser, ids["anonimizado"]))["last_error"] == "cliente_sin_destinatario"
    assert env.contadores.descartadas == 2 and env.contadores.fallos == 0


async def _negocio_con_personal(db, d, cuantos):
    await db.execute(
        "insert into public.tenant_users (id, tenant_id, pending_invite_email) "
        "select gen_random_uuid(), $1, 'personal-' || g || '-' || $2 || '@negocio.test' from generate_series(1, $3) g",
        d["tenant"],
        d["tenant"].hex[:8],
        cuantos,
    )
    await db.execute(
        "insert into public.avisos_negocio_config (tenant_id, tenant_user_id) "
        "select tenant_id, id from public.tenant_users where tenant_id = $1",
        d["tenant"],
    )


async def _encolar_negocio(db, d, template="aviso_negocio_reserva"):
    return await db.fetchval(
        "insert into public.notification_outbox (tenant_id, destino, template, params, next_attempt_at) "
        "values ($1, 'negocio', $2, jsonb_build_object('booking_id', $3::text, 'inicio', $4::text), "
        "now() - interval '1 minute') returning id",
        d["tenant"],
        template,
        str(d["booking"]),
        INICIO.isoformat(),
    )


async def test_un_aviso_al_negocio_sale_por_email_al_personal_configurado_sin_tocar_notifications_log(
    conectar_como, db_superuser, negocios
):
    d = await negocios()
    await _negocio_con_personal(db_superuser, d, 2)
    id_ = await _encolar_negocio(db_superuser, d)
    bot, correo = BotApi(), CorreoSimulado()
    await _enviador(await conectar_como("enviador"), bot, correo).ciclo(asyncio.Event())
    assert bot.pedidos == [] and len(correo.enviados) == 2
    assert {a for _, a, _ in correo.enviados} == {"Nueva reserva"}
    assert (await _fila(db_superuser, id_))["status"] == "sent"
    assert (
        await db_superuser.fetchval("select count(*) from public.notifications_log where tenant_id = $1", d["tenant"])
        == 0
    )


async def test_el_registro_de_entregas_de_la_cantidad_maxima_de_destinatarios_cabe_en_el_check_de_la_tabla(
    conectar_como, db_superuser, negocios
):
    """Peor caso: todos fallan de forma permanente, que es cuando cada entrada lleva su código de error."""
    d = await negocios()
    await _negocio_con_personal(db_superuser, d, MAX_DESTINATARIOS_POR_FILA)
    id_ = await _encolar_negocio(db_superuser, d)
    correo = CorreoSimulado(error_siempre=error_email(422))
    env = _enviador(await conectar_como("enviador"), BotApi(), correo)
    await env.ciclo(asyncio.Event())
    fila = await _fila(db_superuser, id_)
    assert fila["status"] == "failed"
    assert len(json.loads(fila["entregas"])) == MAX_DESTINATARIOS_POR_FILA
    assert len(fila["entregas"]) <= 16000 - 1000
    assert env.contadores.por_canal == {"email": {"sent": 0, "failed": MAX_DESTINATARIOS_POR_FILA}}


async def test_la_sincronizacion_con_calendar_no_se_envia_y_no_se_cierra(conectar_como, db_superuser, negocios):
    d = await negocios()
    id_ = await _encolar_negocio(db_superuser, d, "sincronizacion_calendar")
    bot, correo = BotApi(), CorreoSimulado()
    env = _enviador(await conectar_como("enviador"), bot, correo)
    await env.ciclo(asyncio.Event())
    fila = await _fila(db_superuser, id_)
    assert fila["status"] == "pending" and bot.pedidos == [] and correo.enviados == []
    assert env.contadores.omitidas == 1


def _dsn_del_rol(url_base, rol, password):
    partes = urlsplit(url_base)
    netloc = f"{quote(rol, safe='')}:{quote(password, safe='')}@{partes.hostname}"
    if partes.port:
        netloc += f":{partes.port}"
    return urlunsplit((partes.scheme, netloc, partes.path, partes.query, ""))


@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM real: solo en POSIX")
def test_el_servicio_arranca_con_el_rol_enviador_responde_health_y_se_apaga_limpio_con_sigterm(
    database_url, roles_password, unused_tcp_port_para_enviador
):
    entorno = {
        **os.environ,
        "ENVIADOR_DATABASE_URL": _dsn_del_rol(database_url, "enviador", roles_password),
        "RESEND_API_KEY": "re_clave_de_prueba",
        "EMAIL_REMITENTE": "avisos@agendemos.ar",
        "ENVIADOR_INTERVALO_SEG": "0.2",
        "ENVIADOR_ESPERA_MAXIMA_SEG": "0.5",
        "ENVIADOR_PLAZO_APAGADO_SEG": "2",
        "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),
    }
    entorno.pop("SENTRY_DSN", None)
    proceso = subprocess.Popen(  # noqa: S603 - argumentos fijos, sin shell
        [sys.executable, "-m", "uvicorn", "enviador_app.main:app", "--port", str(unused_tcp_port_para_enviador)],
        env=entorno,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        url = f"http://127.0.0.1:{unused_tcp_port_para_enviador}/health"
        limite = time.monotonic() + 40
        respuesta = None
        while time.monotonic() < limite and proceso.poll() is None:
            try:
                respuesta = httpx.get(url, timeout=2)
            except httpx.HTTPError:
                time.sleep(0.3)
                continue
            if respuesta.status_code == 200:
                break
            time.sleep(0.3)
        assert respuesta is not None and respuesta.status_code == 200 and respuesta.json() == {"estado": "ok"}
        proceso.send_signal(signal.SIGTERM)
        salida, _ = proceso.communicate(timeout=30)
    finally:
        if proceso.poll() is None:
            proceso.kill()
    assert proceso.returncode == 0, salida
    assert "enviador_detenido" in salida


@pytest.fixture
def unused_tcp_port_para_enviador():
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
