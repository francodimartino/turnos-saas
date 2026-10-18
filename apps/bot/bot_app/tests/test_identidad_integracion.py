"""Identidad del cliente contra Postgres real: webhook completo con el pool conectado como `bot_service`.

Telegram y Google Calendar están simulados. Dos tenants para comprobar que nada cruza de uno a otro. Los
teléfonos y nombres son inventados.
"""

import itertools
import json
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from platform_calendar import EventoNoLimpiado

from bot_app import crear_app
from bot_app.identidad import flujo
from bot_app.tests import test_webhook_integracion as base

pytestmark = pytest.mark.integracion

# Fixtures del webhook (pool conectado como `bot_service`, alta de tenants con bot), reutilizadas tal cual.
pool_bot = base.pool_bot
escenarios = base.escenarios
Telegram = base.Telegram
CABECERA = base.CABECERA

asyncpg = pytest.importorskip("asyncpg")

CLAVE_HMAC = "channel_user_hmac_key"
TEL_CRUDO = "5491100000000"
TEL = "+5491100000000"
TEL_B = "+5491100000011"
_contador = itertools.count(1)


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
async def tenants(escenarios, db_superuser, clave_hmac):
    """Dos tenants activos con bot; al terminar borra lo que creó el flujo (antes que `escenarios`)."""
    a, b = await escenarios(), await escenarios()
    try:
        yield a, b
    finally:
        ids = [a.tenant_id, b.tenant_id]
        for tabla in ("notifications_log", "bookings", "end_customers"):
            await db_superuser.execute(f"delete from public.{tabla} where tenant_id = any($1::uuid[])", ids)
        # la integración de Calendar referencia al profesional: va antes que él
        await db_superuser.execute(
            "delete from public.tenant_integrations where tenant_id = any($1::uuid[]) "
            "and integration_type = 'google_calendar'",
            ids,
        )
        for tabla in ("professional_services", "professionals", "services"):
            await db_superuser.execute(f"delete from public.{tabla} where tenant_id = any($1::uuid[])", ids)


class Bot:
    """App real del bot + cliente HTTP contra ella, con Telegram y Calendar simulados."""

    def __init__(self, pool, calendario=None) -> None:
        self.telegram = Telegram()
        self.calendario = calendario
        app = crear_app(
            pool=pool,
            cliente_http=httpx.AsyncClient(transport=httpx.MockTransport(self.telegram.handler)),
            fabrica_calendario=(lambda conn, http: calendario) if calendario else None,
        )
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bot")

    async def enviar(self, e, cuerpo: dict) -> list[dict]:
        antes = len(self.telegram.llamadas)
        r = await self.http.post(
            e.ruta, json={"update_id": next(_contador), **cuerpo}, headers={CABECERA: e.webhook_secret}
        )
        assert r.status_code == 200
        return self.telegram.llamadas[antes:]

    async def texto(self, e, usuario: str, texto: str):
        return await self.enviar(
            e, {"message": {"message_id": 1, "from": {"id": int(usuario)}, "chat": {"id": int(usuario)}, "text": texto}}
        )

    async def contacto(self, e, usuario: str, telefono: str):
        contacto = {"phone_number": telefono, "first_name": "X", "user_id": int(usuario)}
        return await self.enviar(
            e,
            {
                "message": {
                    "message_id": 1,
                    "from": {"id": int(usuario)},
                    "chat": {"id": int(usuario)},
                    "contact": contacto,
                }
            },
        )

    async def boton(self, e, usuario: str, dato: str):
        return await self.enviar(
            e,
            {
                "callback_query": {
                    "id": "cb",
                    "from": {"id": int(usuario)},
                    "data": dato,
                    "message": {"message_id": 1, "chat": {"id": int(usuario)}},
                }
            },
        )


def _textos(llamadas: list[dict]) -> list[str]:
    return [ll["cuerpo"]["text"] for ll in llamadas if ll["url"].endswith("/sendMessage")]


async def _fila(db, tenant, usuario: str):
    return await db.fetchrow(
        "select * from public.end_customers where tenant_id = $1 and channel_user_id = $2", tenant, usuario
    )


async def _alta_completa(bot, e, usuario: str, telefono: str = TEL_CRUDO, nombre: str = "Ana Prueba") -> None:
    await bot.texto(e, usuario, "hola")
    await bot.contacto(e, usuario, telefono)
    await bot.texto(e, usuario, nombre)


async def test_primer_contacto_crea_el_end_customer_con_telefono_e164_y_nombre(pool_bot, db_superuser, tenants):
    a, _ = tenants
    bot = Bot(pool_bot)
    llamadas = await bot.texto(a, "1001", "hola")
    teclado = llamadas[0]["cuerpo"]["reply_markup"]["keyboard"]
    assert teclado[0][0]["request_contact"] is True
    assert await _fila(db_superuser, a.tenant_id, "1001") is None  # sin teléfono verificado no hay fila

    await bot.contacto(a, "1001", TEL_CRUDO)
    await bot.texto(a, "1001", "Ana Prueba")
    fila = await _fila(db_superuser, a.tenant_id, "1001")
    assert (fila["channel"], fila["phone"], fila["name"], fila["deleted_at"]) == ("telegram", TEL, "Ana Prueba", None)
    # ya identificado: el mensaje siguiente pasa al próximo flujo (acá no hay ninguno: "no entendido")
    textos = _textos(await bot.texto(a, "1001", "quiero un turno"))
    assert flujo.TEXTO_PEDIR_TELEFONO not in textos


async def test_un_contacto_ajeno_no_se_acepta(pool_bot, db_superuser, tenants):
    a, _ = tenants
    bot = Bot(pool_bot)
    await bot.texto(a, "1002", "hola")
    contacto = {"phone_number": TEL_CRUDO, "first_name": "X", "user_id": 9999}  # de otra persona
    await bot.enviar(a, {"message": {"message_id": 1, "from": {"id": 1002}, "chat": {"id": 1002}, "contact": contacto}})
    assert await _fila(db_superuser, a.tenant_id, "1002") is None


async def test_si_el_telefono_matchea_una_alta_manual_la_vincula_sin_duplicar(pool_bot, db_superuser, tenants):
    a, b = tenants
    manual_a = await db_superuser.fetchval(
        "insert into public.end_customers (tenant_id, phone, name) values ($1, $2, 'Carga Manual') returning id",
        a.tenant_id,
        TEL,
    )
    manual_b = await db_superuser.fetchval(
        "insert into public.end_customers (tenant_id, phone, name) values ($1, $2, 'Otro Tenant') returning id",
        b.tenant_id,
        TEL,
    )
    bot = Bot(pool_bot)
    await bot.texto(a, "1003", "hola")
    await bot.contacto(a, "1003", "+54 9 11 0000-0000")
    filas = await db_superuser.fetch(
        "select id, channel_user_id from public.end_customers where tenant_id = $1", a.tenant_id
    )
    assert [(f["id"], f["channel_user_id"]) for f in filas] == [(manual_a, "1003")]
    # tenía nombre: el alta termina ahí
    assert (
        await db_superuser.fetchval("select name from public.end_customers where id = $1", manual_a)
    ) == "Carga Manual"
    # la fila del otro tenant con el mismo teléfono no se tocó
    assert (
        await db_superuser.fetchval("select channel_user_id from public.end_customers where id = $1", manual_b)
    ) is None


async def test_una_alta_manual_ya_vinculada_a_otra_cuenta_no_se_pisa(pool_bot, db_superuser, tenants):
    a, _ = tenants
    ocupada = await db_superuser.fetchval(
        "insert into public.end_customers (tenant_id, channel, channel_user_id, phone, name) "
        "values ($1, 'telegram', '2000', $2, 'Titular') returning id",
        a.tenant_id,
        TEL,
    )
    bot = Bot(pool_bot)
    await _alta_completa(bot, a, "1004")
    assert (
        await db_superuser.fetchval("select channel_user_id from public.end_customers where id = $1", ocupada)
    ) == "2000"
    assert (await _fila(db_superuser, a.tenant_id, "1004"))["id"] != ocupada


async def test_mis_datos_devuelve_solo_los_propios_y_nada_de_otro_cliente_ni_tenant(pool_bot, db_superuser, tenants):
    a, b = tenants
    bot = Bot(pool_bot)
    await _alta_completa(bot, a, "1005", TEL_CRUDO, "Ana Prueba")
    await _alta_completa(bot, b, "1005", "5491100000011", "Beto Otro")  # mismo usuario de canal, otro tenant
    await _alta_completa(bot, a, "1006", "5491100000022", "Carla Tercera")
    await db_superuser.execute(
        'update public.end_customers set extra_data = \'{"preferencia": "tarde"}\'::jsonb '
        "where tenant_id = $1 and channel_user_id = '1005'",
        a.tenant_id,
    )

    (texto,) = _textos(await bot.texto(a, "1005", "mis datos"))
    assert TEL in texto and "Ana Prueba" in texto and "preferencia" in texto
    for ajeno in (TEL_B, "Beto Otro", "Carla Tercera", "00022"):
        assert ajeno not in texto
    (texto_b,) = _textos(await bot.texto(b, "1005", "mis datos"))
    assert TEL_B in texto_b and "Beto Otro" in texto_b and "Ana Prueba" not in texto_b


async def _escenario_calendar(db_superuser, tenant, cliente):
    """Profesional con integración de Calendar, un turno confirmado con evento y uno pending futuro."""
    prof, serv = uuid.uuid4(), uuid.uuid4()
    inicio = datetime.now(timezone.utc) + timedelta(days=30)  # noqa: UP017
    await db_superuser.execute(
        "insert into public.professionals (id, tenant_id, name) values ($1, $2, 'Prof')", prof, tenant
    )
    await db_superuser.execute(
        "insert into public.services (id, tenant_id, name, duration_minutes, price) values ($1, $2, 'Corte', 30, 10)",
        serv,
        tenant,
    )
    integracion = await db_superuser.fetchval(
        "insert into public.tenant_integrations (tenant_id, integration_type, professional_id) "
        "values ($1, 'google_calendar', $2) returning id",
        tenant,
        prof,
    )
    evento = f"evt-{uuid.uuid4().hex[:10]}"
    confirmado, pendiente = uuid.uuid4(), uuid.uuid4()
    await db_superuser.execute(
        "insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, "
        "status, google_calendar_event_id) values ($1, $2, $3, $4, $5, tstzrange($6, $7), 'confirmed', $8)",
        confirmado, tenant, cliente, serv, prof, inicio, inicio + timedelta(minutes=30), evento,
    )  # fmt: skip
    await db_superuser.execute(
        "insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, "
        "status, pending_expires_at) values ($1, $2, $3, $4, $5, tstzrange($6, $7), 'pending', $8)",
        pendiente, tenant, cliente, serv, prof, inicio + timedelta(days=1),
        inicio + timedelta(days=1, minutes=30), inicio - timedelta(days=1),
    )  # fmt: skip
    return {"integracion": integracion, "evento": evento, "pendiente": pendiente, "confirmado": confirmado}


class CalendarioEspia:
    """Calendar simulado: anota qué le pidieron y si el borrado ya estaba confirmado en la base al pedírselo."""

    def __init__(self, db, tenant, cliente, fallidos=()) -> None:
        self.db, self.tenant, self.cliente, self._fallidos = db, tenant, cliente, list(fallidos)
        self.pares: list = []
        self.telefono_al_limpiar = "sin llamar"

    async def limpiar_eventos(self, pares):
        self.pares += pares
        # el superusuario solo ve datos confirmados: si ya es null, la anonimización estaba commiteada
        self.telefono_al_limpiar = await self.db.fetchval(
            "select phone from public.end_customers where id = $1", self.cliente
        )
        return self._fallidos


async def test_borrar_mis_datos_confirma_anonimiza_y_limpia_calendar_despues_del_commit(
    pool_bot, db_superuser, tenants
):
    a, b = tenants
    setup = Bot(pool_bot)
    await _alta_completa(setup, a, "1007")
    await _alta_completa(setup, b, "1007", "5491100000011", "Beto Otro")
    cliente = (await _fila(db_superuser, a.tenant_id, "1007"))["id"]
    datos = await _escenario_calendar(db_superuser, a.tenant_id, cliente)
    espia = CalendarioEspia(
        db_superuser, a.tenant_id, cliente, [EventoNoLimpiado(datos["integracion"], datos["evento"], "token_vencido")]
    )
    bot = Bot(pool_bot, espia)

    llamadas = await bot.texto(a, "1007", "borrar mis datos")
    assert len(llamadas[0]["cuerpo"]["reply_markup"]["inline_keyboard"]) == 2
    assert (
        await db_superuser.fetchval("select phone from public.end_customers where id = $1", cliente)
    ) == TEL  # aún no

    textos = _textos(await bot.boton(a, "1007", flujo.CB_BORRAR_SI))
    assert textos == [flujo.TEXTO_BORRADO]
    fila = await db_superuser.fetchrow("select * from public.end_customers where id = $1", cliente)
    assert (fila["phone"], fila["name"], fila["channel_user_id"]) == (None, None, None)
    assert fila["deleted_at"] is not None and fila["channel_user_hash"]
    # el turno pending futuro se canceló (lo hace la función SQL) y el confirmado quedó
    estados = {
        r["id"]: r["status"]
        for r in await db_superuser.fetch("select id, status from public.bookings where end_customer_id = $1", cliente)
    }
    assert estados == {datos["pendiente"]: "cancelled", datos["confirmado"]: "confirmed"}
    # Calendar: se limpió el evento del cliente, con el borrado ya commiteado
    assert espia.pares == [(datos["integracion"], datos["evento"])]
    assert espia.telefono_al_limpiar is None
    # el fallo de limpieza quedó auditado con event_id y motivo, sin PII
    auditoria = await db_superuser.fetch(
        "select metadata from public.audit_log where tenant_id = $1 and action = 'calendar_scrub_failed'", a.tenant_id
    )
    assert [json.loads(f["metadata"]) for f in auditoria] == [{"event_id": datos["evento"], "motivo": "token_vencido"}]
    # el cliente del otro tenant (mismo usuario de canal) no se tocó
    assert (await _fila(db_superuser, b.tenant_id, "1007"))["phone"] == TEL_B
    # "mis datos" ya no encuentra nada del cliente borrado
    assert _textos(await bot.texto(a, "1007", "mis datos")) == [flujo.TEXTO_SIN_DATOS]


async def test_cancelar_el_borrado_no_toca_nada(pool_bot, db_superuser, tenants):
    a, _ = tenants
    bot = Bot(pool_bot)
    await _alta_completa(bot, a, "1008")
    await bot.texto(a, "1008", "borrar mis datos")
    assert _textos(await bot.boton(a, "1008", flujo.CB_BORRAR_NO)) == [flujo.TEXTO_BORRADO_CANCELADO]
    assert (await _fila(db_superuser, a.tenant_id, "1008"))["phone"] == TEL


async def _borrar(bot, e, usuario: str) -> None:
    await bot.texto(e, usuario, "borrar mis datos")
    await bot.boton(e, usuario, flujo.CB_BORRAR_SI)


async def test_recontacto_reactivar_restaura_el_canal_borra_el_hash_y_pide_de_nuevo_los_datos(
    pool_bot, db_superuser, tenants
):
    a, _ = tenants
    bot = Bot(pool_bot)
    await _alta_completa(bot, a, "1009")
    id_ = (await _fila(db_superuser, a.tenant_id, "1009"))["id"]
    await _borrar(bot, a, "1009")

    llamadas = await bot.texto(a, "1009", "hola de nuevo")
    botones = llamadas[0]["cuerpo"]["reply_markup"]["inline_keyboard"]
    assert [b[0]["callback_data"] for b in botones] == [flujo.CB_REACTIVAR, flujo.CB_EMPEZAR_DE_CERO]
    # sin respuesta, nada se reactivó
    fila = await db_superuser.fetchrow("select * from public.end_customers where id = $1", id_)
    assert fila["channel_user_id"] is None and fila["deleted_at"] is not None

    llamadas = await bot.boton(a, "1009", flujo.CB_REACTIVAR)
    fila = await db_superuser.fetchrow("select * from public.end_customers where id = $1", id_)
    assert (fila["channel_user_id"], fila["channel_user_hash"], fila["deleted_at"]) == ("1009", None, None)
    assert (fila["phone"], fila["name"]) == (None, None)  # se vuelven a pedir
    assert llamadas[-1]["cuerpo"]["reply_markup"]["keyboard"][0][0]["request_contact"] is True
    await bot.contacto(a, "1009", "5491100000033")
    await bot.texto(a, "1009", "Ana Nueva")
    fila = await db_superuser.fetchrow("select * from public.end_customers where id = $1", id_)
    assert (fila["phone"], fila["name"]) == ("+5491100000033", "Ana Nueva")
    assert (
        await db_superuser.fetchval("select count(*) from public.end_customers where tenant_id = $1", a.tenant_id) == 1
    )


async def test_recontacto_empezar_de_cero_crea_fila_nueva_y_la_vieja_queda_dormida(pool_bot, db_superuser, tenants):
    a, _ = tenants
    bot = Bot(pool_bot)
    await _alta_completa(bot, a, "1010")
    vieja = (await _fila(db_superuser, a.tenant_id, "1010"))["id"]
    await _borrar(bot, a, "1010")

    await bot.texto(a, "1010", "hola")
    await bot.boton(a, "1010", flujo.CB_EMPEZAR_DE_CERO)
    await bot.contacto(a, "1010", "5491100000044")
    await bot.texto(a, "1010", "Ana Otra Vez")
    nueva = await _fila(db_superuser, a.tenant_id, "1010")
    assert nueva["id"] != vieja and nueva["phone"] == "+5491100000044"
    dormida = await db_superuser.fetchrow("select * from public.end_customers where id = $1", vieja)
    assert dormida["channel_user_id"] is None and dormida["deleted_at"] is not None and dormida["phone"] is None


async def test_el_recontacto_no_cruza_tenants(pool_bot, db_superuser, tenants):
    a, b = tenants
    bot = Bot(pool_bot)
    await _alta_completa(bot, a, "1011")
    await _borrar(bot, a, "1011")
    # en el otro tenant es una persona nueva: no hay perfil dormido que ofrecer
    llamadas = await bot.texto(b, "1011", "hola")
    assert llamadas[0]["cuerpo"]["reply_markup"]["keyboard"][0][0]["request_contact"] is True
