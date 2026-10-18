"""Reserva contra Postgres real: webhook completo con el pool conectado como `bot_service`.

Telegram y Google Calendar están simulados (HTTP mockeado). Cada test usa tenants propios para comprobar que nada
cruza de uno a otro. El profesional atiende un solo día (dentro de 3 días, 10:00 a 12:00) para que los horarios
ofrecidos sean deterministas sin depender de la hora a la que corre el test. Teléfonos y nombres inventados.
"""

import asyncio
import itertools
import json
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from disponibilidad import ZONA_HORARIA
from platform_calendar import AccionSync, ErrorApiCalendar, ResultadoSync
from platform_calendar.config import VARIABLES as VARIABLES_GOOGLE

from bot_app import crear_app
from bot_app.reserva import flujo
from bot_app.tests import test_webhook_integracion as base

pytestmark = pytest.mark.integracion

pool_bot = base.pool_bot
escenarios = base.escenarios
Telegram = base.Telegram
CABECERA = base.CABECERA

asyncpg = pytest.importorskip("asyncpg")

UTC = timezone.utc  # noqa: UP017
CLAVE_HMAC = "channel_user_hmac_key"
_contador = itertools.count(5_000_000)


@pytest.fixture
async def clave_hmac(db_superuser):
    """Siembra la clave HMAC de Vault si no estaba (el alta de clientes consulta perfiles dormidos)."""
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


def _dia_de_atencion() -> tuple[datetime, int]:
    """Dentro de 3 días a las 10:00 (hora de Buenos Aires) y su `day_of_week` (0 = domingo)."""
    dia = (datetime.now(ZONA_HORARIA) + timedelta(days=3)).date()
    inicio = datetime(dia.year, dia.month, dia.day, 10, 0, tzinfo=ZONA_HORARIA).astimezone(UTC)
    return inicio, (dia.weekday() + 1) % 7


class Local:
    """Catálogo sembrado de un tenant."""

    def __init__(self, tenant_id) -> None:
        self.tenant_id = tenant_id
        self.profesional = uuid.uuid4()
        self.profesional_baja = uuid.uuid4()
        self.s_auto = uuid.uuid4()
        self.s_aprobacion = uuid.uuid4()
        self.s_deposit = uuid.uuid4()
        self.s_sin_profesional = uuid.uuid4()
        self.s_profesional_de_baja = uuid.uuid4()


async def _sembrar(db, tenant_id) -> Local:
    L = Local(tenant_id)
    _, dia_semana = _dia_de_atencion()
    for prof, activo in ((L.profesional, True), (L.profesional_baja, False)):
        await db.execute(
            "insert into public.professionals (id, tenant_id, name, is_active) values ($1, $2, $3, $4)",
            prof, tenant_id, "Lucía" if activo else "Baja", activo,
        )  # fmt: skip
        await db.execute(
            "insert into public.availability_rules (tenant_id, professional_id, day_of_week, start_time, end_time) "
            "values ($1, $2, $3, '10:00', '12:00')",
            tenant_id, prof, dia_semana,
        )  # fmt: skip
    servicios = [
        (L.s_auto, "A Corte", None),
        (L.s_aprobacion, "B Coloración con aprobación", None),
        (L.s_deposit, "C Con seña", 1000),
        (L.s_sin_profesional, "D Sin profesional", None),
        (L.s_profesional_de_baja, "E Profesional de baja", None),
    ]
    for id_, nombre, seña in servicios:
        await db.execute(
            "insert into public.services (id, tenant_id, name, duration_minutes, price, deposit_amount) "
            "values ($1, $2, $3, 30, 5000, $4)",
            id_, tenant_id, nombre, seña,
        )  # fmt: skip
    ofertas = [
        (L.profesional, L.s_auto, "auto"),
        (L.profesional, L.s_aprobacion, "approval"),
        (L.profesional, L.s_deposit, "deposit"),
        (L.profesional_baja, L.s_profesional_de_baja, "auto"),
    ]
    for prof, serv, modo in ofertas:
        await db.execute(
            "insert into public.professional_services (tenant_id, professional_id, service_id, confirmation_mode) "
            "values ($1, $2, $3, $4)",
            tenant_id, prof, serv, modo,
        )  # fmt: skip
    return L


@pytest.fixture
async def locales(escenarios, db_superuser, clave_hmac):
    """Dos tenants activos con bot y catálogo; al terminar borra lo que creó el flujo (antes que `escenarios`)."""
    a, b = await escenarios(), await escenarios()
    la, lb = await _sembrar(db_superuser, a.tenant_id), await _sembrar(db_superuser, b.tenant_id)
    try:
        yield (a, la), (b, lb)
    finally:
        ids = [a.tenant_id, b.tenant_id]
        for tabla in ("notifications_log", "bookings", "end_customers"):
            await db_superuser.execute(f"delete from public.{tabla} where tenant_id = any($1::uuid[])", ids)
        await db_superuser.execute(
            "delete from public.tenant_integrations where tenant_id = any($1::uuid[]) "
            "and integration_type = 'google_calendar'",
            ids,
        )
        for tabla in ("professional_services", "availability_rules", "professionals", "services"):
            await db_superuser.execute(f"delete from public.{tabla} where tenant_id = any($1::uuid[])", ids)


class CalendarioFalso:
    """Calendar simulado: anota los pedidos y comprueba que el booking ya estaba commiteado al pedirse."""

    def __init__(self, db, resultado=None, error=None) -> None:
        self.db, self.resultado, self.error = db, resultado, error
        self.pedidos: list = []
        self.booking_visible: list[bool] = []

    async def sincronizar_booking(self, booking):
        self.pedidos.append(booking)
        # el superusuario solo ve datos commiteados
        self.booking_visible.append(
            await self.db.fetchval("select exists(select 1 from public.bookings where id = $1)", booking.id)
        )
        if self.error:
            raise self.error
        return self.resultado


class Bot:
    def __init__(self, pool, calendario=None) -> None:
        self.telegram = Telegram()
        app = crear_app(
            pool=pool,
            cliente_http=httpx.AsyncClient(transport=httpx.MockTransport(self.telegram.handler)),
            fabrica_calendario=(lambda conn, http: calendario) if calendario else None,
        )
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bot")

    async def _enviar(self, e, cuerpo: dict) -> list[dict]:
        # Con envíos concurrentes (la carrera) las llamadas se reparten por chat: se filtra por destinatario.
        chat = str((cuerpo.get("message") or cuerpo["callback_query"]["message"])["chat"]["id"])
        antes = len(self.telegram.llamadas)
        r = await self.http.post(
            e.ruta, json={"update_id": next(_contador), **cuerpo}, headers={CABECERA: e.webhook_secret}
        )
        assert r.status_code == 200
        return [ll for ll in self.telegram.llamadas[antes:] if ll["cuerpo"].get("chat_id") == chat]

    async def texto(self, e, usuario: str, texto: str):
        u = int(usuario)
        return await self._enviar(
            e, {"message": {"message_id": 1, "from": {"id": u}, "chat": {"id": u}, "text": texto}}
        )

    async def contacto(self, e, usuario: str, telefono: str):
        u = int(usuario)
        contacto = {"phone_number": telefono, "first_name": "X", "user_id": u}
        return await self._enviar(
            e, {"message": {"message_id": 1, "from": {"id": u}, "chat": {"id": u}, "contact": contacto}}
        )

    async def boton(self, e, usuario: str, dato: str):
        u = int(usuario)
        origen = {"message_id": 1, "chat": {"id": u}}
        cuerpo = {"callback_query": {"id": "cb", "from": {"id": u}, "data": dato, "message": origen}}
        return await self._enviar(e, cuerpo)

    async def alta(self, e, usuario: str, telefono: str, nombre: str = "Ana Prueba") -> None:
        await self.texto(e, usuario, "hola")
        await self.contacto(e, usuario, telefono)
        await self.texto(e, usuario, nombre)


def textos(llamadas: list[dict]) -> str:
    return "\n".join(ll["cuerpo"]["text"] for ll in llamadas if ll["url"].endswith("/sendMessage"))


def botones(llamadas: list[dict]) -> list[str]:
    datos = []
    for ll in llamadas:
        teclado = ll["cuerpo"].get("reply_markup", {}).get("inline_keyboard", [])
        datos += [b["callback_data"] for fila in teclado for b in fila]
    return datos


async def hasta_confirmar(bot, e, usuario: str, servicio, profesional, inicio: datetime) -> list[dict]:
    await bot.texto(e, usuario, "reservar")
    await bot.boton(e, usuario, f"rsv:s:{servicio}")
    await bot.boton(e, usuario, f"rsv:p:{profesional}")
    return await bot.boton(e, usuario, f"rsv:h:{int(inicio.timestamp())}")


async def bookings(db, tenant_id):
    return await db.fetch(
        "select *, lower(time_range) as inicio, upper(time_range) as fin from public.bookings "
        "where tenant_id = $1 order by created_at",
        tenant_id,
    )


TEL_1, TEL_2 = "5491100000101", "5491100000102"


async def test_auto_reserva_confirmada_con_time_range_y_aviso_al_cliente(pool_bot, db_superuser, locales):
    (a, la), _ = locales
    bot = Bot(pool_bot)
    await bot.alta(a, "3001", TEL_1)
    inicio, _dia = _dia_de_atencion()

    llamadas = await bot.texto(a, "3001", "reservar")
    assert [b for b in botones(llamadas) if b.startswith("rsv:s:")] == [
        f"rsv:s:{la.s_auto}",
        f"rsv:s:{la.s_aprobacion}",
    ]
    llamadas = await bot.boton(a, "3001", f"rsv:s:{la.s_auto}")
    assert botones(llamadas)[0] == f"rsv:p:{la.profesional}"
    llamadas = await bot.boton(a, "3001", f"rsv:p:{la.profesional}")
    assert botones(llamadas)[0] == f"rsv:h:{int(inicio.timestamp())}"  # el primer hueco es el 10:00 de ese día
    llamadas = await bot.boton(a, "3001", f"rsv:h:{int(inicio.timestamp())}")
    assert "rsv:ok" in botones(llamadas)
    assert await bookings(db_superuser, a.tenant_id) == []  # nada se crea hasta confirmar

    llamadas = await bot.boton(a, "3001", "rsv:ok")
    assert flujo.TEXTO_CONFIRMADO in textos(llamadas)
    (fila,) = await bookings(db_superuser, a.tenant_id)
    assert (fila["status"], fila["pending_expires_at"], fila["google_calendar_event_id"]) == ("confirmed", None, None)
    assert (fila["inicio"], fila["fin"]) == (inicio, inicio + timedelta(minutes=30))
    assert (fila["service_id"], fila["professional_id"]) == (la.s_auto, la.profesional)
    cliente = await db_superuser.fetchval(
        "select id from public.end_customers where tenant_id = $1 and channel_user_id = '3001'", a.tenant_id
    )
    assert fila["end_customer_id"] == cliente
    # la confirmación quedó registrada en notifications_log
    registrado = await db_superuser.fetchval(
        "select count(*) from public.notifications_log where end_customer_id = $1 and template = 'reserva_confirmada'",
        cliente,
    )
    assert registrado == 1
    # la sesión de la reserva terminó
    assert "rsv:ok" not in botones(await bot.boton(a, "3001", "rsv:ok"))


async def test_approval_nace_pending_con_vencimiento_y_el_cliente_sabe_que_queda_pendiente(
    pool_bot, db_superuser, locales
):
    (a, la), _ = locales
    bot = Bot(pool_bot)
    await bot.alta(a, "3002", TEL_1)
    inicio, _ = _dia_de_atencion()
    await hasta_confirmar(bot, a, "3002", la.s_aprobacion, la.profesional, inicio)
    llamadas = await bot.boton(a, "3002", "rsv:ok")
    assert "pendiente de aprobación" in textos(llamadas) and flujo.TEXTO_CONFIRMADO not in textos(llamadas)
    (fila,) = await bookings(db_superuser, a.tenant_id)
    assert fila["status"] == "pending"
    esperado = min(datetime.now(UTC) + timedelta(hours=24), inicio)  # default de 24 h
    assert abs(fila["pending_expires_at"] - esperado) < timedelta(minutes=2)


async def test_approval_respeta_el_timeout_configurado_por_el_tenant(pool_bot, db_superuser, locales):
    (a, la), _ = locales
    await db_superuser.execute(
        "update public.tenants set settings = '{\"booking_approval_timeout_hours\": 1}'::jsonb where id = $1",
        a.tenant_id,
    )
    bot = Bot(pool_bot)
    await bot.alta(a, "3003", TEL_1)
    inicio, _ = _dia_de_atencion()
    await hasta_confirmar(bot, a, "3003", la.s_aprobacion, la.profesional, inicio)
    await bot.boton(a, "3003", "rsv:ok")
    (fila,) = await bookings(db_superuser, a.tenant_id)
    assert abs(fila["pending_expires_at"] - (datetime.now(UTC) + timedelta(hours=1))) < timedelta(minutes=2)


async def test_no_se_ofrece_deposit_ni_servicios_sin_profesional_activo(pool_bot, locales):
    (a, la), _ = locales
    bot = Bot(pool_bot)
    await bot.alta(a, "3004", TEL_1)
    ofrecidos = [b for b in botones(await bot.texto(a, "3004", "reservar")) if b.startswith("rsv:s:")]
    assert ofrecidos == [f"rsv:s:{la.s_auto}", f"rsv:s:{la.s_aprobacion}"]
    for no_elegible in (la.s_deposit, la.s_sin_profesional, la.s_profesional_de_baja):
        assert flujo.TEXTO_OPCION_VENCIDA in textos(await bot.boton(a, "3004", f"rsv:s:{no_elegible}"))


async def test_un_boton_forjado_con_ids_de_otro_tenant_no_hace_nada(pool_bot, db_superuser, locales):
    (a, la), (_, lb) = locales
    bot = Bot(pool_bot)
    await bot.alta(a, "3005", TEL_1)
    await bot.texto(a, "3005", "reservar")
    assert flujo.TEXTO_OPCION_VENCIDA in textos(await bot.boton(a, "3005", f"rsv:s:{lb.s_auto}"))
    await bot.boton(a, "3005", f"rsv:s:{la.s_auto}")
    assert flujo.TEXTO_OPCION_VENCIDA in textos(await bot.boton(a, "3005", f"rsv:p:{lb.profesional}"))
    inicio, _ = _dia_de_atencion()
    await bot.boton(a, "3005", f"rsv:p:{la.profesional}")
    await bot.boton(a, "3005", f"rsv:h:{int(inicio.timestamp())}")
    assert [await bookings(db_superuser, t) for t in (a.tenant_id, locales[1][0].tenant_id)] == [[], []]


async def test_un_horario_fuera_de_la_disponibilidad_no_se_acepta(pool_bot, db_superuser, locales):
    (a, la), _ = locales
    bot = Bot(pool_bot)
    await bot.alta(a, "3006", TEL_1)
    inicio, _ = _dia_de_atencion()
    fuera = inicio + timedelta(hours=5)  # 15:00: el profesional no atiende
    llamadas = await hasta_confirmar(bot, a, "3006", la.s_auto, la.profesional, fuera)
    assert flujo.TEXTO_HORARIO_NO_DISPONIBLE in textos(llamadas)
    assert await bookings(db_superuser, a.tenant_id) == []


async def test_el_segundo_cliente_que_confirma_el_mismo_horario_recibe_otros_horarios(pool_bot, db_superuser, locales):
    (a, la), _ = locales
    bot = Bot(pool_bot)
    await bot.alta(a, "3007", TEL_1, "Ana Prueba")
    await bot.alta(a, "3008", TEL_2, "Beto Prueba")
    inicio, _ = _dia_de_atencion()
    await hasta_confirmar(bot, a, "3007", la.s_auto, la.profesional, inicio)
    await hasta_confirmar(bot, a, "3008", la.s_auto, la.profesional, inicio)  # ambos vieron el hueco libre

    primero = await bot.boton(a, "3007", "rsv:ok")
    segundo = await bot.boton(a, "3008", "rsv:ok")
    assert flujo.TEXTO_CONFIRMADO in textos(primero)
    assert flujo.TEXTO_HORARIO_NO_DISPONIBLE in textos(segundo)
    assert f"rsv:h:{int(inicio.timestamp())}" not in botones(segundo)
    assert any(b.startswith("rsv:h:") for b in botones(segundo))
    assert len(await bookings(db_superuser, a.tenant_id)) == 1


async def test_carrera_real_dos_confirmaciones_simultaneas_una_gana_y_la_otra_no_da_500(
    pool_bot, db_superuser, locales
):
    (a, la), _ = locales
    bot = Bot(pool_bot)
    await bot.alta(a, "3009", TEL_1, "Ana Prueba")
    await bot.alta(a, "3010", TEL_2, "Beto Prueba")
    inicio, _ = _dia_de_atencion()
    await hasta_confirmar(bot, a, "3009", la.s_auto, la.profesional, inicio)
    await hasta_confirmar(bot, a, "3010", la.s_auto, la.profesional, inicio)

    # Las dos validaciones previas ven el hueco libre; el exclusion constraint decide al insertar. Si alguna
    # respuesta fuera 500, `Bot._enviar` lo detectaría (assert del status).
    r1, r2 = await asyncio.gather(bot.boton(a, "3009", "rsv:ok"), bot.boton(a, "3010", "rsv:ok"))
    resultados = [textos(r1), textos(r2)]
    assert sum(flujo.TEXTO_CONFIRMADO in t for t in resultados) == 1
    assert sum(flujo.TEXTO_HORARIO_NO_DISPONIBLE in t for t in resultados) == 1
    perdedor = r1 if flujo.TEXTO_HORARIO_NO_DISPONIBLE in resultados[0] else r2
    assert any(b.startswith("rsv:h:") for b in botones(perdedor))
    (fila,) = await bookings(db_superuser, a.tenant_id)
    assert fila["status"] == "confirmed"


async def test_el_mismo_horario_en_otro_tenant_no_choca(pool_bot, db_superuser, locales):
    (a, la), (b, lb) = locales
    bot = Bot(pool_bot)
    await bot.alta(a, "3011", TEL_1)
    await bot.alta(b, "3011", TEL_1)
    inicio, _ = _dia_de_atencion()
    for e, loc in ((a, la), (b, lb)):
        await hasta_confirmar(bot, e, "3011", loc.s_auto, loc.profesional, inicio)
        assert flujo.TEXTO_CONFIRMADO in textos(await bot.boton(e, "3011", "rsv:ok"))
    assert len(await bookings(db_superuser, a.tenant_id)) == len(await bookings(db_superuser, b.tenant_id)) == 1


async def _con_integracion(db, tenant_id, profesional) -> None:
    await db.execute(
        "insert into public.tenant_integrations (tenant_id, integration_type, professional_id) "
        "values ($1, 'google_calendar', $2)",
        tenant_id,
        profesional,
    )


async def test_confirmed_crea_el_evento_despues_del_commit_y_guarda_su_id(pool_bot, db_superuser, locales):
    (a, la), _ = locales
    await _con_integracion(db_superuser, a.tenant_id, la.profesional)
    calendario = CalendarioFalso(db_superuser, ResultadoSync(AccionSync.CREADO, "evt-prueba-1"))
    bot = Bot(pool_bot, calendario)
    await bot.alta(a, "3012", TEL_1)
    inicio, _ = _dia_de_atencion()
    await hasta_confirmar(bot, a, "3012", la.s_auto, la.profesional, inicio)
    assert calendario.pedidos == []  # confirmar la reserva todavía no se hizo
    await bot.boton(a, "3012", "rsv:ok")
    (pedido,) = calendario.pedidos
    assert calendario.booking_visible == [True]
    assert (pedido.status, pedido.professional_id, pedido.servicio, pedido.inicio) == (
        "confirmed", la.profesional, "A Corte", inicio,
    )  # fmt: skip
    (fila,) = await bookings(db_superuser, a.tenant_id)
    assert fila["google_calendar_event_id"] == "evt-prueba-1"


async def test_pending_nunca_va_al_calendario(pool_bot, db_superuser, locales):
    (a, la), _ = locales
    await _con_integracion(db_superuser, a.tenant_id, la.profesional)
    calendario = CalendarioFalso(db_superuser, ResultadoSync(AccionSync.CREADO, "evt-no-debe-existir"))
    bot = Bot(pool_bot, calendario)
    await bot.alta(a, "3013", TEL_1)
    inicio, _ = _dia_de_atencion()
    await hasta_confirmar(bot, a, "3013", la.s_aprobacion, la.profesional, inicio)
    await bot.boton(a, "3013", "rsv:ok")
    assert calendario.pedidos == []
    (fila,) = await bookings(db_superuser, a.tenant_id)
    assert (fila["status"], fila["google_calendar_event_id"]) == ("pending", None)


@pytest.mark.parametrize("caso", ["sin_integracion", "google_falla", "calendar_no_configurado"], ids=lambda c: c)
async def test_un_problema_de_calendar_no_impide_la_reserva(pool_bot, db_superuser, locales, caso, monkeypatch):
    for variable in VARIABLES_GOOGLE:
        monkeypatch.delenv(variable, raising=False)
    (a, la), _ = locales
    calendario = None
    if caso == "sin_integracion":
        calendario = CalendarioFalso(db_superuser, ResultadoSync(AccionSync.SIN_CONEXION, None))
    elif caso == "google_falla":
        await _con_integracion(db_superuser, a.tenant_id, la.profesional)
        calendario = CalendarioFalso(db_superuser, error=ErrorApiCalendar(503))
    # "calendar_no_configurado": sin fábrica inyectada se usa la real, y sin variables de Google no puede armarse
    bot = Bot(pool_bot, calendario)
    await bot.alta(a, "3014", TEL_1)
    inicio, _ = _dia_de_atencion()
    await hasta_confirmar(bot, a, "3014", la.s_auto, la.profesional, inicio)
    llamadas = await bot.boton(a, "3014", "rsv:ok")
    assert flujo.TEXTO_CONFIRMADO in textos(llamadas)
    (fila,) = await bookings(db_superuser, a.tenant_id)
    assert (fila["status"], fila["google_calendar_event_id"]) == ("confirmed", None)


async def test_cancelar_un_turno_ya_reservado_no_lo_toca_el_bot(pool_bot, db_superuser, locales):
    (a, la), _ = locales
    bot = Bot(pool_bot)
    await bot.alta(a, "3015", TEL_1)
    inicio, _ = _dia_de_atencion()
    await hasta_confirmar(bot, a, "3015", la.s_auto, la.profesional, inicio)
    await bot.boton(a, "3015", "rsv:ok")
    for texto in ("quiero cancelar mi turno", "reprogramar"):
        await bot.texto(a, "3015", texto)  # lo atiende consultas
    (fila,) = await bookings(db_superuser, a.tenant_id)
    assert fila["status"] == "confirmed"


async def test_sin_alta_completa_pide_el_dato_antes_de_ofrecer_servicios(pool_bot, locales):
    (a, _), _ = locales
    bot = Bot(pool_bot)
    llamadas = await bot.texto(a, "3016", "reservar")
    assert not any(b.startswith("rsv:") for b in botones(llamadas))
    assert flujo.TEXTO_ELEGIR_SERVICIO not in textos(llamadas)


# --- bot vs dashboard sobre el mismo horario ---------------------------------------------------------------------

_INSERTAR_COMO_DASHBOARD = (
    "insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status) "
    "values ($1, $2, $3, $4, tstzrange($5::timestamptz, $6::timestamptz, '[)'), 'confirmed')"
)


async def _alta_como_dashboard(conn, tenant_id, cliente, local, inicio, retener=None) -> None:
    """Misma secuencia que `POST /turnos`: transacción, lock compartido por profesional y después el insert."""
    from turnos_queries.bloqueo import bloquear_profesional

    async with conn.transaction():
        await conn.execute("select set_config('app.current_tenant_id', $1, true)", str(tenant_id))
        await bloquear_profesional(conn, tenant_id=tenant_id, profesional_id=local.profesional)
        await conn.execute(
            _INSERTAR_COMO_DASHBOARD,
            tenant_id, cliente, local.s_auto, local.profesional, inicio, inicio + timedelta(minutes=30),
        )  # fmt: skip
        if retener is not None:
            await retener()


async def _cliente_del_dashboard(db, tenant_id) -> uuid.UUID:
    return await db.fetchval(
        "insert into public.end_customers (tenant_id, channel, channel_user_id, phone, name) "
        "values ($1, 'telegram', $2, $3, 'Cliente del dashboard') returning id",
        tenant_id, f"dash-{uuid.uuid4().hex[:8]}", f"+54911666{uuid.uuid4().int % 10**7:07d}",
    )  # fmt: skip


async def test_bot_y_dashboard_simultaneos_sobre_el_mismo_horario_dejan_un_solo_turno(
    pool_bot, db_superuser, conectar_como, locales
):
    (a, la), _ = locales
    bot = Bot(pool_bot)
    inicio, _ = _dia_de_atencion()
    dashboard = await conectar_como("dashboard_service")
    cliente = await _cliente_del_dashboard(db_superuser, a.tenant_id)
    await bot.alta(a, "3011", TEL_1, "Ana Prueba")
    await hasta_confirmar(bot, a, "3011", la.s_auto, la.profesional, inicio)  # el bot vio el hueco libre

    # El dashboard toma el lock, inserta y retiene la transacción: el bot tiene que esperar (no chocar en el índice)
    # y, cuando el dashboard confirma, recibir el "ya no está disponible" limpio con horarios nuevos.
    tomado, soltar = asyncio.Event(), asyncio.Event()

    async def retener() -> None:
        tomado.set()
        await soltar.wait()

    alta = asyncio.create_task(_alta_como_dashboard(dashboard, a.tenant_id, cliente, la, inicio, retener))
    await asyncio.wait_for(tomado.wait(), 10)
    confirmacion = asyncio.create_task(bot.boton(a, "3011", "rsv:ok"))
    await asyncio.sleep(0.5)
    assert not confirmacion.done()  # esperando el lock del profesional
    soltar.set()
    await alta
    respuesta = await asyncio.wait_for(confirmacion, 15)
    assert flujo.TEXTO_HORARIO_NO_DISPONIBLE in textos(respuesta)
    assert any(b.startswith("rsv:h:") for b in botones(respuesta))
    (fila,) = await bookings(db_superuser, a.tenant_id)
    assert fila["end_customer_id"] == cliente


async def test_carreras_repetidas_bot_vs_dashboard_nunca_dan_500_ni_dos_turnos(
    pool_bot, db_superuser, conectar_como, locales
):
    (a, la), _ = locales
    # El bot confirma varias veces seguidas para un mismo cliente: se sube el límite por hora para no mezclarlo.
    await db_superuser.execute(
        "update public.tenants set settings = '{\"booking_max_per_hour\": 60}'::jsonb where id = $1", a.tenant_id
    )
    bot = Bot(pool_bot)
    dashboard = await conectar_como("dashboard_service")
    cliente = await _cliente_del_dashboard(db_superuser, a.tenant_id)
    inicio, _ = _dia_de_atencion()
    await bot.alta(a, "3012", TEL_2, "Beto Prueba")
    for ronda in range(4):
        desde = inicio + timedelta(minutes=30 * ronda)  # un hueco de 30 min por ronda (disponibilidad de 10 a 12)
        await hasta_confirmar(bot, a, "3012", la.s_auto, la.profesional, desde)
        # `Bot._enviar` falla si el webhook responde algo distinto de 200 (un deadlock sin manejar sería 500).
        r_bot, r_dash = await asyncio.gather(
            bot.boton(a, "3012", "rsv:ok"),
            _alta_como_dashboard(dashboard, a.tenant_id, cliente, la, desde),
            return_exceptions=True,
        )
        assert not isinstance(r_bot, Exception), (ronda, r_bot)
        # El único error aceptable del lado del dashboard es el choque limpio (23P01), nunca un deadlock.
        assert r_dash is None or getattr(r_dash, "sqlstate", None) == "23P01", (ronda, r_dash)
        vigentes = await db_superuser.fetchval(
            "select count(*) from public.bookings where professional_id = $1 and status <> 'cancelled' "
            "and time_range && tstzrange($2, $3)",
            la.profesional, desde, desde + timedelta(minutes=30),
        )  # fmt: skip
        # Si ganó el bot, el insert del dashboard recibió 23P01 (esperado en la carrera): igual queda uno solo.
        assert vigentes == 1, ronda


# --- límites de reserva (tope de activos, límite por hora, anticipación mínima) ---------------------------------


async def _settings(db, tenant_id, **claves) -> None:
    await db.execute("update public.tenants set settings = $2::jsonb where id = $1", tenant_id, json.dumps(claves))


async def _reservar(bot, e, usuario: str, local, desde: datetime, minutos: int) -> list[dict]:
    """Reserva (servicio auto) el hueco que empieza `minutos` después de `desde` y devuelve la respuesta a `rsv:ok`."""
    inicio = desde + timedelta(minutes=minutos)
    await hasta_confirmar(bot, e, usuario, local.s_auto, local.profesional, inicio)
    return await bot.boton(e, usuario, "rsv:ok")


async def test_el_tope_de_turnos_activos_rechaza_sin_crear_y_cancelar_uno_libera_el_cupo(
    pool_bot, db_superuser, locales
):
    (a, la), _ = locales
    await _settings(db_superuser, a.tenant_id, booking_max_active_per_customer=2, booking_max_per_hour=60)
    bot = Bot(pool_bot)
    await bot.alta(a, "3021", TEL_1)
    await _cliente_del_dashboard(db_superuser, a.tenant_id)  # otro cliente del tenant: sus datos no se filtran
    inicio, _ = _dia_de_atencion()
    assert flujo.TEXTO_CONFIRMADO in textos(await _reservar(bot, a, "3021", la, inicio, 0))
    assert flujo.TEXTO_CONFIRMADO in textos(await _reservar(bot, a, "3021", la, inicio, 30))

    rechazo = textos(await _reservar(bot, a, "3021", la, inicio, 60))
    assert "Ya tenés 2 turnos pendientes o confirmados" in rechazo
    assert flujo.TEXTO_CONFIRMADO not in rechazo and "Cliente del dashboard" not in rechazo
    filas = await bookings(db_superuser, a.tenant_id)
    assert len(filas) == 2

    await db_superuser.execute(
        "update public.bookings set status = 'cancelled', pending_expires_at = null where id = $1", filas[0]["id"]
    )
    assert flujo.TEXTO_CONFIRMADO in textos(await _reservar(bot, a, "3021", la, inicio, 60))
    assert len(await bookings(db_superuser, a.tenant_id)) == 3


async def test_el_tope_cuenta_pending_y_no_cuenta_turnos_de_otro_cliente(pool_bot, db_superuser, locales):
    (a, la), _ = locales
    await _settings(db_superuser, a.tenant_id, booking_max_active_per_customer=1, booking_max_per_hour=60)
    bot = Bot(pool_bot)
    await bot.alta(a, "3022", TEL_1)
    await bot.alta(a, "3023", TEL_2, "Beto Prueba")
    inicio, _ = _dia_de_atencion()
    await hasta_confirmar(bot, a, "3022", la.s_aprobacion, la.profesional, inicio)
    await bot.boton(a, "3022", "rsv:ok")  # nace pending
    assert flujo.TEXTO_CONFIRMADO in textos(await _reservar(bot, a, "3023", la, inicio, 30))  # otro cliente: libre
    rechazo = textos(await _reservar(bot, a, "3022", la, inicio, 60))
    assert "Ya tenés 1 turnos pendientes o confirmados" in rechazo
    assert "Beto" not in rechazo
    assert len(await bookings(db_superuser, a.tenant_id)) == 2


async def test_el_limite_por_hora_cuenta_las_reservas_creadas_en_los_ultimos_60_minutos(
    pool_bot, db_superuser, locales
):
    (a, la), _ = locales
    await _settings(db_superuser, a.tenant_id, booking_max_active_per_customer=50, booking_max_per_hour=2)
    bot = Bot(pool_bot)
    await bot.alta(a, "3024", TEL_1)
    inicio, _ = _dia_de_atencion()
    for minutos in (0, 30):
        assert flujo.TEXTO_CONFIRMADO in textos(await _reservar(bot, a, "3024", la, inicio, minutos))

    rechazo = textos(await _reservar(bot, a, "3024", la, inicio, 60))
    assert flujo.TEXTO_LIMITE_POR_HORA in rechazo
    assert len(await bookings(db_superuser, a.tenant_id)) == 2

    # Una reserva de hace más de una hora ya no cuenta, aunque siga activa.
    await db_superuser.execute(
        "update public.bookings set created_at = now() - interval '61 minutes' where tenant_id = $1", a.tenant_id
    )
    assert flujo.TEXTO_CONFIRMADO in textos(await _reservar(bot, a, "3024", la, inicio, 60))
    assert len(await bookings(db_superuser, a.tenant_id)) == 3


async def test_sin_settings_aplican_los_defaults_3_reservas_por_hora(pool_bot, db_superuser, locales):
    (a, la), _ = locales
    bot = Bot(pool_bot)
    await bot.alta(a, "3025", TEL_1)
    inicio, _ = _dia_de_atencion()
    for minutos in (0, 30, 60):
        assert flujo.TEXTO_CONFIRMADO in textos(await _reservar(bot, a, "3025", la, inicio, minutos))
    assert flujo.TEXTO_LIMITE_POR_HORA in textos(await _reservar(bot, a, "3025", la, inicio, 90))
    assert len(await bookings(db_superuser, a.tenant_id)) == 3


async def test_un_inicio_dentro_de_la_anticipacion_se_rechaza_en_la_transaccion_que_crea_el_turno(
    pool_bot, db_superuser, locales
):
    from platform_tenancy import tenant_tx

    from bot_app.reserva import limites, repositorio

    (a, la), _ = locales
    cliente = await _cliente_del_dashboard(db_superuser, a.tenant_id)
    ahora = datetime.now(UTC)

    async def crear(inicio: datetime):
        async with tenant_tx(pool_bot, a.tenant_id) as conn:
            return await repositorio.crear_booking(
                conn, tenant_id=a.tenant_id, end_customer_id=cliente, servicio_id=la.s_auto,
                profesional_id=la.profesional, inicio=inicio, fin=inicio + timedelta(minutes=30),
                pending_expires_at=None, limites=await repositorio.limites_reserva(conn, a.tenant_id), ahora=ahora,
            )  # fmt: skip

    with pytest.raises(limites.ReservaRechazada) as error:
        await crear(ahora + timedelta(minutes=10))
    assert error.value.motivo == limites.MOTIVO_ANTICIPACION
    assert await bookings(db_superuser, a.tenant_id) == []
    assert (await crear(ahora + timedelta(minutes=31))) is not None  # el default es de 30 minutos
    assert len(await bookings(db_superuser, a.tenant_id)) == 1


async def test_los_turnos_del_staff_desde_el_dashboard_no_estan_sujetos_a_los_limites(
    pool_bot, db_superuser, conectar_como, locales
):
    (a, la), _ = locales
    await _settings(db_superuser, a.tenant_id, booking_max_active_per_customer=1, booking_max_per_hour=1)
    dashboard = await conectar_como("dashboard_service")
    cliente = await _cliente_del_dashboard(db_superuser, a.tenant_id)
    inicio, _ = _dia_de_atencion()
    for minutos in (0, 30, 60):
        await _alta_como_dashboard(dashboard, a.tenant_id, cliente, la, inicio + timedelta(minutes=minutos))
    assert len(await bookings(db_superuser, a.tenant_id)) == 3


async def _pendiente_de(db, a, la, usuario: str, inicio: datetime, vence_en: timedelta) -> uuid.UUID:
    """Siembra un pending del cliente `usuario` (ya dado de alta) sobre `inicio`, vencido si `vence_en` < 0."""
    cliente = await db.fetchval(
        "select id from public.end_customers where tenant_id = $1 and channel_user_id = $2", a.tenant_id, usuario
    )
    return await db.fetchval(
        "insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status, "
        "pending_expires_at) values ($1, $2, $3, $4, tstzrange($5, $6), 'pending', $7) returning id",
        a.tenant_id, cliente, la.s_aprobacion, la.profesional, inicio, inicio + timedelta(minutes=30),
        datetime.now(UTC) + vence_en,
    )  # fmt: skip


async def test_reservar_sobre_un_pendiente_vencido_funciona_lo_libera_y_avisa_una_sola_vez(
    pool_bot, db_superuser, locales
):
    (a, la), _ = locales
    bot = Bot(pool_bot)
    await bot.alta(a, "3101", TEL_1, "Ana Prueba")
    await bot.alta(a, "3102", TEL_2, "Beto Prueba")
    inicio, _ = _dia_de_atencion()
    vencido = await _pendiente_de(db_superuser, a, la, "3101", inicio, timedelta(minutes=-5))

    # el horario ocupado solo por un pendiente vencido se ofrece
    llamadas = await bot.texto(a, "3102", "reservar")
    assert f"rsv:s:{la.s_auto}" in botones(llamadas)
    llamadas = await hasta_confirmar(bot, a, "3102", la.s_auto, la.profesional, inicio)
    assert "rsv:ok" in botones(llamadas)

    antes = len(bot.telegram.llamadas)
    llamadas = await bot.boton(a, "3102", "rsv:ok")
    assert flujo.TEXTO_CONFIRMADO in textos(llamadas)
    estados = {f["id"]: f["status"] for f in await bookings(db_superuser, a.tenant_id)}
    assert estados[vencido] == "cancelled" and list(estados.values()).count("confirmed") == 1

    # el cliente liberado recibe el aviso de vencimiento y queda en notifications_log
    avisos = [
        ll
        for ll in bot.telegram.llamadas[antes:]
        if ll["cuerpo"].get("chat_id") == "3101" and "venció" in ll["cuerpo"].get("text", "")
    ]
    assert len(avisos) == 1
    cliente = await db_superuser.fetchval(
        "select id from public.end_customers where tenant_id = $1 and channel_user_id = '3101'", a.tenant_id
    )
    registros = await db_superuser.fetch(
        "select status from public.notifications_log where end_customer_id = $1 and template = 'turno_vencido'",
        cliente,
    )
    assert [r["status"] for r in registros] == ["sent"]


async def test_reservar_sobre_un_pendiente_vigente_sigue_rechazado_y_no_avisa(pool_bot, db_superuser, locales):
    (a, la), _ = locales
    bot = Bot(pool_bot)
    await bot.alta(a, "3103", TEL_1, "Ana Prueba")
    await bot.alta(a, "3104", TEL_2, "Beto Prueba")
    inicio, _ = _dia_de_atencion()
    # ambos ven el hueco libre; después el cliente 3103 queda con un pendiente vigente sobre el horario
    await hasta_confirmar(bot, a, "3104", la.s_auto, la.profesional, inicio)
    vigente = await _pendiente_de(db_superuser, a, la, "3103", inicio, timedelta(hours=2))

    antes = len(bot.telegram.llamadas)
    llamadas = await bot.boton(a, "3104", "rsv:ok")
    assert flujo.TEXTO_HORARIO_NO_DISPONIBLE in textos(llamadas)
    (fila,) = await bookings(db_superuser, a.tenant_id)
    assert fila["id"] == vigente and fila["status"] == "pending"
    assert not [ll for ll in bot.telegram.llamadas[antes:] if "venció" in ll["cuerpo"].get("text", "")]
