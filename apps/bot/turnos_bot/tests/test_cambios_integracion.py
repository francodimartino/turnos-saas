"""Cancelar y mover un turno por el bot contra Postgres real: webhook completo con el pool como `bot_service`.

Dos tenants con catálogo propio para comprobar que una persona no toca el turno de otra ni de otro negocio, que cada
cambio queda en `audit_log` con la propia persona como actor, que los avisos al negocio y al cliente salen del outbox en
la misma transacción y que Calendar se actualiza después del commit. Telegram y Google Calendar están simulados;
nombres y teléfonos inventados.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from platform_calendar import AccionSync, ResultadoSync

from turnos_bot import webhook
from turnos_bot.cambios import flujo
from turnos_bot.tests import test_reserva_integracion as base

pytestmark = pytest.mark.integracion

pool_bot = base.pool_bot
escenarios = base.escenarios
clave_hmac = base.clave_hmac
locales = base.locales
Bot = base.Bot
botones = base.botones
textos = base.textos
bookings = base.bookings
_dia_de_atencion = base._dia_de_atencion
_con_integracion = base._con_integracion

UTC = timezone.utc  # noqa: UP017
TEL_1, TEL_2 = base.TEL_1, base.TEL_2
PLAZO_720 = "Este turno ya no se puede cancelar por acá: hace falta hacerlo con al menos 720 horas de anticipación."
SQL_CLIENTE = (
    "select end_customer_id from public.canales_cliente "
    "where tenant_id = $1 and canal = 'telegram' and identificador = $2"
)


async def _cliente(db, e, usuario: str) -> uuid.UUID:
    return await db.fetchval(SQL_CLIENTE, e.tenant_id, usuario)


async def _sembrar_turno(
    db, e, loc, usuario: str, servicio, inicio: datetime, estado: str = "confirmed", evento: str | None = None
) -> uuid.UUID:
    """Un turno de la persona `usuario` (ya dada de alta), sin pasar por el bot."""
    vence = datetime.now(UTC) + timedelta(hours=2) if estado == "pending" else None
    return await db.fetchval(
        "insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status, "
        "pending_expires_at, google_calendar_event_id) values ($1, $2, $3, $4, tstzrange($5, $6), $7, $8, $9) "
        "returning id",
        e.tenant_id, await _cliente(db, e, usuario), servicio, loc.profesional, inicio,
        inicio + timedelta(minutes=30), estado, vence, evento,
    )  # fmt: skip


async def _auditoria(db, tenant_id) -> list:
    return await db.fetch(
        "select action, actor_type, actor_id, entity, entity_id, metadata from public.audit_log "
        "where tenant_id = $1 and entity = 'bookings' order by created_at, id",
        tenant_id,
    )


async def _outbox(db, tenant_id, destino: str) -> list:
    return await db.fetch(
        "select template, params, status, next_attempt_at from public.notification_outbox "
        "where tenant_id = $1 and destino = $2 order by created_at, id",
        tenant_id,
        destino,
    )


async def _sin_cambios(db, e, antes: list) -> None:
    """Nada se escribió: los turnos son los de antes, sin auditoría de turnos ni avisos al negocio."""
    despues = await bookings(db, e.tenant_id)
    assert [(f["id"], f["status"], f["inicio"], f["starts_at_propuesto"]) for f in despues] == [
        (f["id"], f["status"], f["inicio"], f["starts_at_propuesto"]) for f in antes
    ]
    assert await _auditoria(db, e.tenant_id) == [] and await _outbox(db, e.tenant_id, "negocio") == []


class CalendarioConBorrado(base.CalendarioFalso):
    def __init__(self, db, resultado) -> None:
        super().__init__(db, resultado)
        self.retirados: list = []

    async def retirar_evento(self, profesional_id, id_externo):
        self.retirados.append((profesional_id, id_externo))
        return ResultadoSync(AccionSync.BORRADO, None)


# --- mis turnos y cancelar -----------------------------------------------------------------------------------


async def test_mis_turnos_ofrece_cancelar_o_mover_cada_turno_con_su_id_opaco(pool_bot, db_superuser, locales):
    (a, la), _ = locales
    bot = Bot(pool_bot)
    await bot.alta(a, "4001", TEL_1)
    inicio, _ = _dia_de_atencion()
    turno = await _sembrar_turno(db_superuser, a, la, "4001", la.s_auto, inicio)

    llamadas = await bot.boton(a, "4001", "con:turnos")
    assert f"cam:t:{turno}" in botones(llamadas)
    assert str(turno) not in textos(llamadas)  # el id viaja en el botón, nunca en el texto
    llamadas = await bot.boton(a, "4001", f"cam:t:{turno}")
    assert botones(llamadas)[:3] == [f"cam:c:{turno}", f"cam:m:{turno}", "con:turnos"]


async def test_cancelar_dentro_del_plazo_deja_todo_en_la_misma_transaccion(pool_bot, db_superuser, locales):
    (a, la), _ = locales
    bot = Bot(pool_bot)
    await bot.alta(a, "4002", TEL_1)
    inicio, _ = _dia_de_atencion()
    turno = await _sembrar_turno(db_superuser, a, la, "4002", la.s_auto, inicio)
    persona = await _cliente(db_superuser, a, "4002")

    llamadas = await bot.boton(a, "4002", f"cam:c:{turno}")
    assert f"cam:cc:{turno}" in botones(llamadas)
    assert (await bookings(db_superuser, a.tenant_id))[0]["status"] == "confirmed"  # pedir confirmar no cancela

    llamadas = await bot.boton(a, "4002", f"cam:cc:{turno}")
    assert "Listo, cancelamos tu turno de A Corte" in textos(llamadas)
    (fila,) = await bookings(db_superuser, a.tenant_id)
    assert fila["status"] == "cancelled"

    # auditoría: la propia persona es el actor y el canal es el bot
    (constancia,) = await _auditoria(db_superuser, a.tenant_id)
    assert (constancia["action"], constancia["actor_type"], constancia["actor_id"]) == (
        "booking_cancelled_by_customer",
        "bot",
        persona,
    )
    assert constancia["entity_id"] == turno and json.loads(constancia["metadata"])["canal"] == "bot"

    # avisos: al negocio (encolado, pendiente) y al cliente (único Telegram: se envió y la fila se cerró)
    (negocio,) = await _outbox(db_superuser, a.tenant_id, "negocio")
    assert negocio["template"] == "aviso_negocio_cancelacion" and negocio["status"] == "pending"
    assert json.loads(negocio["params"]) == {"booking_id": str(turno), "inicio": inicio.isoformat()}
    (cliente,) = await _outbox(db_superuser, a.tenant_id, "cliente")
    assert cliente["template"] == "turno_cancelado" and cliente["status"] == "sent"
    assert json.loads(cliente["params"]) == {"booking_id": str(turno), "inicio": inicio.isoformat()}


async def test_cancelar_con_otro_canal_verificado_encola_pero_no_cierra_la_fila_del_cliente(
    pool_bot, db_superuser, locales
):
    (a, la), _ = locales
    bot = Bot(pool_bot)
    await bot.alta(a, "4003", TEL_1)
    persona = await _cliente(db_superuser, a, "4003")
    await db_superuser.execute(
        "insert into public.canales_cliente (tenant_id, end_customer_id, canal, identificador, verificado_at) "
        "values ($1, $2, 'email', $3, now())",
        a.tenant_id, persona, f"{uuid.uuid4().hex[:8]}@example.test",
    )  # fmt: skip
    inicio, _ = _dia_de_atencion()
    turno = await _sembrar_turno(db_superuser, a, la, "4003", la.s_auto, inicio)

    llamadas = await bot.boton(a, "4003", f"cam:cc:{turno}")
    assert "Listo, cancelamos" in textos(llamadas)  # la conversación igual contesta por el chat
    (cliente,) = await _outbox(db_superuser, a.tenant_id, "cliente")
    assert (cliente["template"], cliente["status"]) == ("turno_cancelado", "pending")
    (negocio,) = await _outbox(db_superuser, a.tenant_id, "negocio")
    assert negocio["status"] == "pending"


async def test_cancelar_pasado_el_plazo_indica_las_horas_y_el_contacto_y_no_escribe(pool_bot, db_superuser, locales):
    (a, la), _ = locales
    await db_superuser.execute(
        "update public.tenants set settings = $2::jsonb where id = $1",
        a.tenant_id,
        json.dumps({"cancelacion_plazo_horas": 720, "local_contacto": "Tel. 11 5555-0000"}),
    )
    bot = Bot(pool_bot)
    await bot.alta(a, "4004", TEL_1)
    inicio, _ = _dia_de_atencion()
    turno = await _sembrar_turno(db_superuser, a, la, "4004", la.s_auto, inicio)
    antes = await bookings(db_superuser, a.tenant_id)

    llamadas = await bot.boton(a, "4004", f"cam:c:{turno}")  # el plazo se comprueba ya al empezar
    assert textos(llamadas) == PLAZO_720 + " Comunicate con el negocio: Tel. 11 5555-0000"
    assert not any(b.startswith("cam:cc:") for b in botones(llamadas))
    llamadas = await bot.boton(a, "4004", f"cam:cc:{turno}")  # y el dominio lo vuelve a comprobar al confirmar
    assert textos(llamadas) == PLAZO_720 + " Comunicate con el negocio: Tel. 11 5555-0000"
    await _sin_cambios(db_superuser, a, antes)
    assert await _outbox(db_superuser, a.tenant_id, "cliente") == []


async def test_dentro_del_plazo_configurado_por_el_negocio_se_puede_cancelar(pool_bot, db_superuser, locales):
    (a, la), _ = locales
    await db_superuser.execute(
        "update public.tenants set settings = $2::jsonb where id = $1",
        a.tenant_id,
        json.dumps({"cancelacion_plazo_horas": 1}),
    )
    bot = Bot(pool_bot)
    await bot.alta(a, "4005", TEL_1)
    inicio, _ = _dia_de_atencion()
    turno = await _sembrar_turno(db_superuser, a, la, "4005", la.s_auto, inicio)
    await bot.boton(a, "4005", f"cam:cc:{turno}")
    assert (await bookings(db_superuser, a.tenant_id))[0]["status"] == "cancelled"


async def test_un_turno_pasado_no_se_cancela_por_el_bot(pool_bot, db_superuser, locales):
    (a, la), _ = locales
    bot = Bot(pool_bot)
    await bot.alta(a, "4006", TEL_1)
    pasado = datetime.now(UTC) - timedelta(hours=3)
    turno = await _sembrar_turno(db_superuser, a, la, "4006", la.s_auto, pasado)
    antes = await bookings(db_superuser, a.tenant_id)

    llamadas = await bot.boton(a, "4006", f"cam:cc:{turno}")
    assert textos(llamadas) == (
        "Este turno ya no se puede cancelar por acá: hace falta hacerlo con al menos 24 horas de anticipación. "
        "Comunicate directamente con el negocio."
    )
    await _sin_cambios(db_superuser, a, antes)


async def test_el_callback_repetido_de_cancelar_no_cancela_dos_veces_ni_duplica_avisos(pool_bot, db_superuser, locales):
    (a, la), _ = locales
    bot = Bot(pool_bot)
    await bot.alta(a, "4007", TEL_1)
    inicio, _ = _dia_de_atencion()
    turno = await _sembrar_turno(db_superuser, a, la, "4007", la.s_auto, inicio)

    await bot.boton(a, "4007", f"cam:cc:{turno}")
    llamadas = await bot.boton(a, "4007", f"cam:cc:{turno}")
    assert flujo.TEXTO_NO_MODIFICABLE in textos(llamadas)
    assert len(await _auditoria(db_superuser, a.tenant_id)) == 1
    assert len(await _outbox(db_superuser, a.tenant_id, "negocio")) == 1
    assert len(await _outbox(db_superuser, a.tenant_id, "cliente")) == 1


async def test_un_turno_ya_cancelado_por_el_negocio_no_se_ofrece_ni_se_cancela_de_nuevo(
    pool_bot, db_superuser, locales
):
    (a, la), _ = locales
    bot = Bot(pool_bot)
    await bot.alta(a, "4008", TEL_1)
    inicio, _ = _dia_de_atencion()
    turno = await _sembrar_turno(db_superuser, a, la, "4008", la.s_auto, inicio, estado="cancelled")
    for dato in (f"cam:t:{turno}", f"cam:c:{turno}", f"cam:cc:{turno}", f"cam:m:{turno}"):
        assert flujo.TEXTO_NO_MODIFICABLE in textos(await bot.boton(a, "4008", dato))
    assert await _auditoria(db_superuser, a.tenant_id) == []


# --- aislamiento ---------------------------------------------------------------------------------------------


async def test_una_persona_no_cancela_ni_mueve_el_turno_de_otra_forjando_el_callback(pool_bot, db_superuser, locales):
    (a, la), _ = locales
    bot = Bot(pool_bot)
    await bot.alta(a, "4009", TEL_1, "Ana Prueba")
    await bot.alta(a, "4010", TEL_2, "Beto Prueba")
    inicio, _ = _dia_de_atencion()
    ajeno = await _sembrar_turno(db_superuser, a, la, "4009", la.s_auto, inicio)
    antes = await bookings(db_superuser, a.tenant_id)

    for dato in (f"cam:t:{ajeno}", f"cam:c:{ajeno}", f"cam:cc:{ajeno}", f"cam:m:{ajeno}"):
        llamadas = await bot.boton(a, "4010", dato)
        assert textos(llamadas) == flujo.TEXTO_TURNO_NO_DISPONIBLE
        assert not any(b.startswith(("cam:cc:", "cam:h:", "cam:ok")) for b in botones(llamadas))
    # con la sesión de mover de la propia persona tampoco se llega al turno ajeno: `cam:ok` no trae id
    await bot.boton(a, "4010", "cam:ok")
    await _sin_cambios(db_superuser, a, antes)


async def test_una_persona_no_toca_el_turno_de_otro_tenant_aunque_conozca_su_id(pool_bot, db_superuser, locales):
    (a, la), (b, lb) = locales
    bot = Bot(pool_bot)
    await bot.alta(a, "4011", TEL_1, "Ana Prueba")
    await bot.alta(b, "4011", TEL_1, "Ana Prueba")  # mismo usuario de Telegram en el otro negocio
    inicio, _ = _dia_de_atencion()
    turno_b = await _sembrar_turno(db_superuser, b, lb, "4011", lb.s_auto, inicio)
    antes_b = await bookings(db_superuser, b.tenant_id)

    for dato in (f"cam:cc:{turno_b}", f"cam:m:{turno_b}"):
        assert textos(await bot.boton(a, "4011", dato)) == flujo.TEXTO_TURNO_NO_DISPONIBLE
    await _sin_cambios(db_superuser, b, antes_b)
    assert (
        await _auditoria(db_superuser, a.tenant_id) == [] and await _outbox(db_superuser, a.tenant_id, "negocio") == []
    )


async def test_cada_negocio_cancela_solo_lo_suyo_y_audita_en_su_tenant(pool_bot, db_superuser, locales):
    (a, la), (b, lb) = locales
    bot = Bot(pool_bot)
    await bot.alta(a, "4012", TEL_1)
    await bot.alta(b, "4012", TEL_1)
    inicio, _ = _dia_de_atencion()
    turno_a = await _sembrar_turno(db_superuser, a, la, "4012", la.s_auto, inicio)
    turno_b = await _sembrar_turno(db_superuser, b, lb, "4012", lb.s_auto, inicio)

    await bot.boton(a, "4012", f"cam:cc:{turno_a}")
    assert [f["status"] for f in await bookings(db_superuser, b.tenant_id)] == ["confirmed"]
    assert [f["entity_id"] for f in await _auditoria(db_superuser, a.tenant_id)] == [turno_a]
    assert await _auditoria(db_superuser, b.tenant_id) == []
    assert len(await _outbox(db_superuser, a.tenant_id, "negocio")) == 1
    assert await _outbox(db_superuser, b.tenant_id, "negocio") == []

    await bot.boton(b, "4012", f"cam:cc:{turno_b}")
    assert [f["entity_id"] for f in await _auditoria(db_superuser, b.tenant_id)] == [turno_b]


# --- mover ---------------------------------------------------------------------------------------------------


async def _elegir_horario(bot, e, usuario: str, turno) -> tuple[list[dict], int]:
    """Abre el movimiento y devuelve la respuesta con el epoch del primer horario que no se solapa con el turno."""
    llamadas = await bot.boton(e, usuario, f"cam:m:{turno}")
    libre_desde = int((_dia_de_atencion()[0] + timedelta(minutes=30)).timestamp())
    ofrecidos = [int(b.removeprefix("cam:h:")) for b in botones(llamadas) if b.startswith("cam:h:")]
    assert ofrecidos, textos(llamadas)
    return llamadas, next(epoch for epoch in ofrecidos if epoch >= libre_desde)


async def test_mover_un_servicio_automatico_cambia_el_horario_audita_avisa_y_actualiza_calendar(
    pool_bot, db_superuser, locales
):
    (a, la), _ = locales
    await _con_integracion(db_superuser, a.tenant_id, la.profesional)
    bot = Bot(pool_bot)
    await bot.alta(a, "4013", TEL_1)
    persona = await _cliente(db_superuser, a, "4013")
    inicio, _ = _dia_de_atencion()
    turno = await _sembrar_turno(db_superuser, a, la, "4013", la.s_auto, inicio, evento="evt-prueba-1")
    calendario = CalendarioConBorrado(db_superuser, ResultadoSync(AccionSync.ACTUALIZADO, "evt-prueba-1"))
    bot = Bot(pool_bot, calendario)

    _, epoch = await _elegir_horario(bot, a, "4013", turno)
    nuevo = datetime.fromtimestamp(epoch, UTC)
    assert nuevo != inicio
    llamadas = await bot.boton(a, "4013", f"cam:h:{epoch}")
    assert "cam:ok" in botones(llamadas)
    assert (await bookings(db_superuser, a.tenant_id))[0]["inicio"] == inicio  # elegir no mueve

    llamadas = await bot.boton(a, "4013", "cam:ok")
    assert "Listo, moví tu turno de A Corte" in textos(llamadas)
    (fila,) = await bookings(db_superuser, a.tenant_id)
    assert (fila["status"], fila["inicio"], fila["fin"]) == ("confirmed", nuevo, nuevo + timedelta(minutes=30))
    assert fila["starts_at_propuesto"] is None

    (constancia,) = await _auditoria(db_superuser, a.tenant_id)
    assert (constancia["action"], constancia["actor_type"], constancia["actor_id"], constancia["entity_id"]) == (
        "booking_moved_by_customer",
        "bot",
        persona,
        turno,
    )
    (negocio,) = await _outbox(db_superuser, a.tenant_id, "negocio")
    assert negocio["template"] == "aviso_negocio_movimiento"
    assert json.loads(negocio["params"]) == {"booking_id": str(turno), "inicio": nuevo.isoformat()}
    (cliente,) = await _outbox(db_superuser, a.tenant_id, "cliente")
    assert (cliente["template"], cliente["status"]) == ("turno_movido", "sent")
    # Calendar: se reescribió el mismo evento en el horario nuevo, después del commit
    ((profesional, evento),) = calendario.pedidos
    assert (profesional, evento.inicio, evento.id_externo) == (la.profesional, nuevo, "evt-prueba-1")
    assert calendario.booking_visible == [True]
    # la sesión de mover terminó
    assert textos(await bot.boton(a, "4013", "cam:ok")) == flujo.TEXTO_OPCION_VENCIDA


async def test_mover_un_servicio_con_aprobacion_mantiene_el_horario_original_hasta_que_responda_el_negocio(
    pool_bot, db_superuser, locales
):
    (a, la), _ = locales
    await _con_integracion(db_superuser, a.tenant_id, la.profesional)
    calendario = CalendarioConBorrado(db_superuser, ResultadoSync(AccionSync.ACTUALIZADO, "evt-2"))
    bot = Bot(pool_bot, calendario)
    await bot.alta(a, "4014", TEL_1)
    persona = await _cliente(db_superuser, a, "4014")
    inicio, _ = _dia_de_atencion()
    turno = await _sembrar_turno(db_superuser, a, la, "4014", la.s_aprobacion, inicio, evento="evt-2")

    _, epoch = await _elegir_horario(bot, a, "4014", turno)
    await bot.boton(a, "4014", f"cam:h:{epoch}")
    llamadas = await bot.boton(a, "4014", "cam:ok")
    assert "el horario original se mantiene" in textos(llamadas)
    assert "hasta que responda" in textos(llamadas)

    (fila,) = await bookings(db_superuser, a.tenant_id)
    nuevo = datetime.fromtimestamp(epoch, UTC)
    assert (fila["status"], fila["inicio"]) == ("confirmed", inicio)  # el original sigue reservado
    assert fila["starts_at_propuesto"] == nuevo and fila["cambio_pendiente_expira_at"] is not None
    (constancia,) = await _auditoria(db_superuser, a.tenant_id)
    assert (constancia["action"], constancia["actor_type"], constancia["actor_id"]) == (
        "booking_move_requested",
        "bot",
        persona,
    )
    (negocio,) = await _outbox(db_superuser, a.tenant_id, "negocio")
    assert negocio["template"] == "aviso_negocio_aprobacion"
    assert json.loads(negocio["params"]) == {"booking_id": str(turno), "inicio": nuevo.isoformat()}
    assert await _outbox(db_superuser, a.tenant_id, "cliente") == []
    assert calendario.pedidos == [] and calendario.retirados == []  # el evento sigue en el horario original

    # un segundo pedido mientras el primero espera: lo dice y no escribe nada más
    _, epoch2 = await _elegir_horario(bot, a, "4014", turno)
    await bot.boton(a, "4014", f"cam:h:{epoch2}")
    llamadas = await bot.boton(a, "4014", "cam:ok")
    assert textos(llamadas) == flujo.TEXTO_CAMBIO_YA_PEDIDO
    assert len(await _auditoria(db_superuser, a.tenant_id)) == 1
    assert len(await _outbox(db_superuser, a.tenant_id, "negocio")) == 1


async def test_mover_un_turno_pendiente_lo_mueve_directo_y_no_toca_calendar(pool_bot, db_superuser, locales):
    (a, la), _ = locales
    calendario = CalendarioConBorrado(db_superuser, ResultadoSync(AccionSync.CREADO, "evt-no"))
    bot = Bot(pool_bot, calendario)
    await bot.alta(a, "4015", TEL_1)
    inicio, _ = _dia_de_atencion()
    turno = await _sembrar_turno(db_superuser, a, la, "4015", la.s_aprobacion, inicio, estado="pending")

    _, epoch = await _elegir_horario(bot, a, "4015", turno)
    await bot.boton(a, "4015", f"cam:h:{epoch}")
    await bot.boton(a, "4015", "cam:ok")
    (fila,) = await bookings(db_superuser, a.tenant_id)
    assert (fila["status"], fila["inicio"]) == ("pending", datetime.fromtimestamp(epoch, UTC))
    assert calendario.pedidos == [] and calendario.retirados == []  # un pendiente nunca va al calendario


async def test_un_horario_tomado_por_otra_persona_antes_de_confirmar_ofrece_otros(pool_bot, db_superuser, locales):
    (a, la), _ = locales
    bot = Bot(pool_bot)
    await bot.alta(a, "4016", TEL_1, "Ana Prueba")
    await bot.alta(a, "4017", TEL_2, "Beto Prueba")
    inicio, _ = _dia_de_atencion()
    turno = await _sembrar_turno(db_superuser, a, la, "4016", la.s_auto, inicio)
    _, epoch = await _elegir_horario(bot, a, "4016", turno)
    await bot.boton(a, "4016", f"cam:h:{epoch}")
    # otra persona se queda con ese horario antes de que la primera confirme
    await _sembrar_turno(db_superuser, a, la, "4017", la.s_auto, datetime.fromtimestamp(epoch, UTC))
    antes = await bookings(db_superuser, a.tenant_id)

    llamadas = await bot.boton(a, "4016", "cam:ok")
    assert flujo.TEXTO_HORARIO_NO_DISPONIBLE in textos(llamadas)
    assert f"cam:h:{epoch}" not in botones(llamadas) and any(b.startswith("cam:h:") for b in botones(llamadas))
    await _sin_cambios(db_superuser, a, antes)


async def test_cancelar_a_mitad_del_movimiento_no_cambia_nada(pool_bot, db_superuser, locales):
    (a, la), _ = locales
    bot = Bot(pool_bot)
    await bot.alta(a, "4018", TEL_1)
    inicio, _ = _dia_de_atencion()
    turno = await _sembrar_turno(db_superuser, a, la, "4018", la.s_auto, inicio)
    antes = await bookings(db_superuser, a.tenant_id)

    _, epoch = await _elegir_horario(bot, a, "4018", turno)
    await bot.boton(a, "4018", f"cam:h:{epoch}")
    assert textos(await bot.boton(a, "4018", "cam:x")) == flujo.TEXTO_SIN_CAMBIOS
    assert textos(await bot.boton(a, "4018", "cam:ok")) == flujo.TEXTO_OPCION_VENCIDA  # botón viejo

    _, epoch = await _elegir_horario(bot, a, "4018", turno)
    assert textos(await bot.texto(a, "4018", "cancelar")) == flujo.TEXTO_SIN_CAMBIOS
    assert textos(await bot.boton(a, "4018", f"cam:h:{epoch}")) == flujo.TEXTO_OPCION_VENCIDA
    await _sin_cambios(db_superuser, a, antes)
    assert (
        await db_superuser.fetchval("select count(*) from public.bot_sessions where tenant_id = $1", a.tenant_id) == 0
    )


async def test_el_turno_cancelado_a_mitad_del_movimiento_termina_el_flujo_sin_mover(pool_bot, db_superuser, locales):
    (a, la), _ = locales
    bot = Bot(pool_bot)
    await bot.alta(a, "4019", TEL_1)
    inicio, _ = _dia_de_atencion()
    turno = await _sembrar_turno(db_superuser, a, la, "4019", la.s_auto, inicio)
    _, epoch = await _elegir_horario(bot, a, "4019", turno)
    await bot.boton(a, "4019", f"cam:h:{epoch}")
    await db_superuser.execute("update public.bookings set status = 'cancelled' where id = $1", turno)

    llamadas = await bot.boton(a, "4019", "cam:ok")
    assert textos(llamadas) == flujo.TEXTO_NO_MODIFICABLE
    (fila,) = await bookings(db_superuser, a.tenant_id)
    assert (fila["status"], fila["inicio"]) == ("cancelled", inicio)
    assert await _auditoria(db_superuser, a.tenant_id) == []


async def test_mover_fuera_de_plazo_lo_dice_al_empezar_sin_ofrecer_horarios_ni_abrir_sesion(
    pool_bot, db_superuser, locales
):
    (a, la), _ = locales
    await db_superuser.execute(
        "update public.tenants set settings = $2::jsonb where id = $1",
        a.tenant_id,
        json.dumps({"cancelacion_plazo_horas": 720, "local_contacto": "Tel. 11 5555-0000"}),
    )
    bot = Bot(pool_bot)
    await bot.alta(a, "4020", TEL_1)
    inicio, _ = _dia_de_atencion()
    turno = await _sembrar_turno(db_superuser, a, la, "4020", la.s_auto, inicio)
    antes = await bookings(db_superuser, a.tenant_id)

    llamadas = await bot.boton(a, "4020", f"cam:m:{turno}")
    assert textos(llamadas) == (
        "Este turno ya no se puede mover por acá: hace falta hacerlo con al menos 720 horas de anticipación. "
        "Comunicate con el negocio: Tel. 11 5555-0000"
    )
    assert not any(b.startswith("cam:h:") for b in botones(llamadas))
    await _sin_cambios(db_superuser, a, antes)
    assert (
        await db_superuser.fetchval("select count(*) from public.bot_sessions where tenant_id = $1", a.tenant_id) == 0
    )


async def test_el_plazo_que_vence_entre_el_inicio_y_la_confirmacion_lo_frena_el_dominio(
    pool_bot, db_superuser, locales
):
    (a, la), _ = locales
    bot = Bot(pool_bot)
    await bot.alta(a, "4025", TEL_1)
    inicio, _ = _dia_de_atencion()
    turno = await _sembrar_turno(db_superuser, a, la, "4025", la.s_auto, inicio)
    antes = await bookings(db_superuser, a.tenant_id)
    _, epoch = await _elegir_horario(bot, a, "4025", turno)
    await bot.boton(a, "4025", f"cam:h:{epoch}")
    await db_superuser.execute(  # el negocio endurece el plazo mientras la persona decide
        "update public.tenants set settings = $2::jsonb where id = $1",
        a.tenant_id,
        json.dumps({"cancelacion_plazo_horas": 720}),
    )

    llamadas = await bot.boton(a, "4025", "cam:ok")
    assert "al menos 720 horas de anticipación" in textos(llamadas)
    await _sin_cambios(db_superuser, a, antes)


async def test_se_puede_correr_un_turno_a_un_horario_que_se_solapa_con_el_actual(pool_bot, db_superuser, locales):
    (a, la), _ = locales
    bot = Bot(pool_bot)
    await bot.alta(a, "4026", TEL_1)
    inicio, _ = _dia_de_atencion()
    turno = await _sembrar_turno(db_superuser, a, la, "4026", la.s_auto, inicio)
    corrido = inicio + timedelta(minutes=15)  # el turno dura 30 minutos: 10:15 se solapa con 10:00

    llamadas, _ = await _elegir_horario(bot, a, "4026", turno)
    assert f"cam:h:{int(corrido.timestamp())}" in botones(llamadas)
    assert f"cam:h:{int(inicio.timestamp())}" not in botones(llamadas)  # el horario actual no es un destino
    await bot.boton(a, "4026", f"cam:h:{int(corrido.timestamp())}")
    await bot.boton(a, "4026", "cam:ok")
    (fila,) = await bookings(db_superuser, a.tenant_id)
    assert (fila["inicio"], fila["fin"]) == (corrido, corrido + timedelta(minutes=30))


async def _boton_sin_esperar_200(bot, e, usuario: str, dato: str, update_id: int) -> int:
    u = int(usuario)
    origen = {"message_id": 1, "chat": {"id": u}}
    cuerpo = {
        "update_id": update_id,
        "callback_query": {"id": "cb", "from": {"id": u}, "data": dato, "message": origen},
    }
    r = await bot.http.post(e.ruta, json=cuerpo, headers={base.CABECERA: e.webhook_secret})
    return r.status_code


def _fallar_al_encolar_el_aviso_del_cliente(monkeypatch) -> None:
    async def falla(*args, **kwargs):
        raise RuntimeError("falla después de haber cambiado el turno y encolado el aviso al negocio")

    monkeypatch.setattr(webhook, "encolar_aviso_segun_canales", falla)


async def test_si_falla_algo_despues_de_encolar_la_cancelacion_no_queda_ni_el_cambio_ni_la_auditoria_ni_los_avisos(
    pool_bot, db_superuser, locales, monkeypatch
):
    (a, la), _ = locales
    bot = Bot(pool_bot)
    await bot.alta(a, "4027", TEL_1)
    inicio, _ = _dia_de_atencion()
    turno = await _sembrar_turno(db_superuser, a, la, "4027", la.s_auto, inicio)
    antes = await bookings(db_superuser, a.tenant_id)
    update_id = next(base._contador)

    with monkeypatch.context() as parche:
        _fallar_al_encolar_el_aviso_del_cliente(parche)
        assert await _boton_sin_esperar_200(bot, a, "4027", f"cam:cc:{turno}", update_id) == 500
    await _sin_cambios(db_superuser, a, antes)  # ni cambio, ni audit_log, ni aviso al negocio
    assert await _outbox(db_superuser, a.tenant_id, "cliente") == []

    # la marca de deduplicación también se revirtió: la misma entrega se reprocesa y ahora sí cancela
    assert await _boton_sin_esperar_200(bot, a, "4027", f"cam:cc:{turno}", update_id) == 200
    assert (await bookings(db_superuser, a.tenant_id))[0]["status"] == "cancelled"
    assert len(await _auditoria(db_superuser, a.tenant_id)) == 1
    assert len(await _outbox(db_superuser, a.tenant_id, "negocio")) == 1


async def test_si_falla_algo_despues_de_encolar_el_movimiento_no_queda_ni_el_cambio_ni_la_auditoria_ni_los_avisos(
    pool_bot, db_superuser, locales, monkeypatch
):
    (a, la), _ = locales
    bot = Bot(pool_bot)
    await bot.alta(a, "4028", TEL_1)
    inicio, _ = _dia_de_atencion()
    turno = await _sembrar_turno(db_superuser, a, la, "4028", la.s_auto, inicio)
    antes = await bookings(db_superuser, a.tenant_id)
    _, epoch = await _elegir_horario(bot, a, "4028", turno)
    await bot.boton(a, "4028", f"cam:h:{epoch}")
    update_id = next(base._contador)

    with monkeypatch.context() as parche:
        _fallar_al_encolar_el_aviso_del_cliente(parche)
        assert await _boton_sin_esperar_200(bot, a, "4028", "cam:ok", update_id) == 500
    await _sin_cambios(db_superuser, a, antes)
    assert await _outbox(db_superuser, a.tenant_id, "cliente") == []

    assert await _boton_sin_esperar_200(bot, a, "4028", "cam:ok", update_id) == 200
    (fila,) = await bookings(db_superuser, a.tenant_id)
    assert fila["inicio"] == datetime.fromtimestamp(epoch, UTC)
    assert len(await _auditoria(db_superuser, a.tenant_id)) == 1


async def test_mover_libera_el_pendiente_vencido_del_horario_nuevo_y_le_avisa(pool_bot, db_superuser, locales):
    (a, la), _ = locales
    bot = Bot(pool_bot)
    await bot.alta(a, "4021", TEL_1, "Ana Prueba")
    await bot.alta(a, "4022", TEL_2, "Beto Prueba")
    inicio, _ = _dia_de_atencion()
    turno = await _sembrar_turno(db_superuser, a, la, "4021", la.s_auto, inicio)
    _, epoch = await _elegir_horario(bot, a, "4021", turno)
    nuevo = datetime.fromtimestamp(epoch, UTC)
    vencido = await base._pendiente_de(db_superuser, a, la, "4022", nuevo, timedelta(minutes=-5))
    await bot.boton(a, "4021", f"cam:h:{epoch}")

    antes = len(bot.telegram.llamadas)
    await bot.boton(a, "4021", "cam:ok")
    estados = {f["id"]: f["status"] for f in await bookings(db_superuser, a.tenant_id)}
    assert estados == {turno: "confirmed", vencido: "cancelled"}
    avisos = [ll for ll in bot.telegram.llamadas[antes:] if "venció" in ll["cuerpo"].get("text", "")]
    assert [ll["cuerpo"]["chat_id"] for ll in avisos] == ["4022"]
    (liberado,) = [f for f in await _outbox(db_superuser, a.tenant_id, "cliente") if f["template"] == "turno_vencido"]
    assert liberado["status"] == "sent"
    acciones = sorted(c["action"] for c in await _auditoria(db_superuser, a.tenant_id))
    assert acciones == ["booking_moved_by_customer", "booking_pending_released_on_move"]


# --- cancelar con Calendar y avisos --------------------------------------------------------------------------


async def test_cancelar_borra_el_evento_de_calendar_despues_del_commit_y_limpia_su_id(pool_bot, db_superuser, locales):
    (a, la), _ = locales
    await _con_integracion(db_superuser, a.tenant_id, la.profesional)
    bot = Bot(pool_bot)
    await bot.alta(a, "4023", TEL_1)
    inicio, _ = _dia_de_atencion()
    turno = await _sembrar_turno(db_superuser, a, la, "4023", la.s_auto, inicio, evento="evt-borrar")
    calendario = CalendarioConBorrado(db_superuser, ResultadoSync(AccionSync.ACTUALIZADO, None))
    bot = Bot(pool_bot, calendario)

    await bot.boton(a, "4023", f"cam:cc:{turno}")
    assert calendario.retirados == [(la.profesional, "evt-borrar")]
    (fila,) = await bookings(db_superuser, a.tenant_id)
    assert (fila["status"], fila["google_calendar_event_id"]) == ("cancelled", None)


async def test_la_sesion_de_mover_se_guarda_tipada_y_versionada(pool_bot, db_superuser, locales):
    (a, la), _ = locales
    bot = Bot(pool_bot)
    await bot.alta(a, "4024", TEL_1)
    inicio, _ = _dia_de_atencion()
    turno = await _sembrar_turno(db_superuser, a, la, "4024", la.s_auto, inicio)
    await bot.boton(a, "4024", f"cam:m:{turno}")
    estado = await db_superuser.fetchval(
        "select state from public.bot_sessions where tenant_id = $1 and channel_user_id = '4024'", a.tenant_id
    )
    guardado = json.loads(estado) if isinstance(estado, str) else estado
    assert guardado["version"] == 2
    assert guardado["cambio"] == {"version": 1, "paso": "horario", "booking_id": str(turno), "pagina": 0}
