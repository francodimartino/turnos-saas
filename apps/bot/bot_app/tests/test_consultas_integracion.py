"""Consultas del cliente contra Postgres real: webhook completo con el pool conectado como `bot_service`.

Dos tenants y varios clientes para comprobar que un cliente no ve turnos de otro ni de otro tenant, y que los
servicios y los datos del local son los del tenant del bot. Telegram está simulado; nombres y teléfonos inventados.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from bot_app.consultas import flujo, local
from bot_app.tests import test_identidad_integracion as base

pytestmark = pytest.mark.integracion

# Fixtures del webhook y de la identidad (pool como `bot_service`, dos tenants con bot), reutilizadas tal cual.
pool_bot = base.pool_bot
escenarios = base.escenarios
clave_hmac = base.clave_hmac
tenants = base.tenants
Bot = base.Bot
_alta_completa = base._alta_completa
_fila = base._fila
_textos = base._textos

UTC = timezone.utc  # noqa: UP017
ANA, BETO = "2001", "2002"


async def _servicio(db, tenant, nombre, precio=100, activo=True) -> uuid.UUID:
    return await db.fetchval(
        "insert into public.services (tenant_id, name, duration_minutes, price, is_active) "
        "values ($1, $2, 30, $3, $4) returning id",
        tenant,
        nombre,
        precio,
        activo,
    )


async def _profesional(db, tenant, nombre) -> uuid.UUID:
    return await db.fetchval(
        "insert into public.professionals (tenant_id, name) values ($1, $2) returning id", tenant, nombre
    )


async def _turno(db, tenant, cliente, servicio, profesional, inicio, status="confirmed") -> None:
    vence = inicio - timedelta(hours=1) if status == "pending" else None
    await db.execute(
        "insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status, "
        "pending_expires_at) values ($1, $2, $3, $4, tstzrange($5, $6), $7, $8)",
        tenant,
        cliente,
        servicio,
        profesional,
        inicio,
        inicio + timedelta(minutes=30),
        status,
        vence,
    )


async def _id_cliente(db, tenant, usuario) -> uuid.UUID:
    return (await _fila(db, tenant, usuario))["id"]


async def test_mis_turnos_solo_muestra_los_futuros_no_cancelados_del_propio_cliente(pool_bot, db_superuser, tenants):
    a, b = tenants
    bot = Bot(pool_bot)
    for e in (a, b):
        await _alta_completa(bot, e, ANA, nombre="Ana Prueba")
    await _alta_completa(bot, a, BETO, "5491100000011", "Beto Otro")
    ana_a, beto_a, ana_b = (
        await _id_cliente(db_superuser, a.tenant_id, ANA),
        await _id_cliente(db_superuser, a.tenant_id, BETO),
        await _id_cliente(db_superuser, b.tenant_id, ANA),
    )
    corte = await _servicio(db_superuser, a.tenant_id, "Corte de ana")
    prof = await _profesional(db_superuser, a.tenant_id, "Lucía")
    tinte_b = await _servicio(db_superuser, b.tenant_id, "Tinte solo de B", 900)
    prof_b = await _profesional(db_superuser, b.tenant_id, "Marta de B")
    ahora = datetime.now(UTC)
    # de Ana en A: dos próximos (uno pendiente), uno cancelado, uno pasado
    await _turno(db_superuser, a.tenant_id, ana_a, corte, prof, ahora + timedelta(days=2))
    await _turno(db_superuser, a.tenant_id, ana_a, corte, prof, ahora + timedelta(days=3), "pending")
    await _turno(db_superuser, a.tenant_id, ana_a, corte, prof, ahora + timedelta(days=4), "cancelled")
    await _turno(db_superuser, a.tenant_id, ana_a, corte, prof, ahora - timedelta(days=2), "completed")
    # de otro cliente del mismo tenant y de la "misma" persona en el otro tenant
    await _turno(db_superuser, a.tenant_id, beto_a, corte, prof, ahora + timedelta(days=5))
    await _turno(db_superuser, b.tenant_id, ana_b, tinte_b, prof_b, ahora + timedelta(days=6))

    texto = _textos(await bot.boton(a, ANA, flujo.CB_TURNOS))[0]

    assert texto.count("Corte de ana con Lucía") == 2
    assert "(pendiente de confirmación)" in texto and texto.count("- ") == 2
    assert "Tinte solo de B" not in texto and "Marta de B" not in texto
    # desde el otro tenant, la misma persona solo ve el suyo
    texto_b = _textos(await bot.boton(b, ANA, flujo.CB_TURNOS))[0]
    assert "Tinte solo de B con Marta de B" in texto_b and "Corte de ana" not in texto_b
    # Beto ve únicamente el suyo
    texto_beto = _textos(await bot.texto(a, BETO, "mis turnos"))[0]
    assert texto_beto.count("Corte de ana con Lucía") == 1


async def test_mis_turnos_sin_turnos_y_ningun_texto_expone_ids(pool_bot, db_superuser, tenants):
    a, _ = tenants
    bot = Bot(pool_bot)
    await _alta_completa(bot, a, ANA)
    assert _textos(await bot.boton(a, ANA, flujo.CB_TURNOS)) == [flujo.TEXTO_SIN_TURNOS]

    ana = await _id_cliente(db_superuser, a.tenant_id, ANA)
    servicio = await _servicio(db_superuser, a.tenant_id, "Corte")
    prof = await _profesional(db_superuser, a.tenant_id, "Lucía")
    await _turno(db_superuser, a.tenant_id, ana, servicio, prof, datetime.now(UTC) + timedelta(days=1))
    for dato in (flujo.CB_TURNOS, flujo.CB_SERVICIOS, flujo.CB_LOCAL, flujo.CB_NEGOCIO, flujo.CB_MENU):
        for texto in _textos(await bot.boton(a, ANA, dato)):
            assert not any(str(i) in texto for i in (ana, servicio, prof, a.tenant_id))


async def _ofrecer(db, tenant, profesional, servicio) -> None:
    await db.execute(
        "insert into public.professional_services (tenant_id, professional_id, service_id, confirmation_mode) "
        "values ($1, $2, $3, 'auto')",
        tenant,
        profesional,
        servicio,
    )


async def test_servicios_y_precios_lista_solo_los_reservables_del_tenant(pool_bot, db_superuser, tenants):
    a, b = tenants
    bot = Bot(pool_bot)
    await _alta_completa(bot, a, ANA)
    prof = await _profesional(db_superuser, a.tenant_id, "Prof A")
    prof_baja = await _profesional(db_superuser, a.tenant_id, "Prof de baja")
    await db_superuser.execute("update public.professionals set is_active = false where id = $1", prof_baja)
    corte = await _servicio(db_superuser, a.tenant_id, "Corte", 12500)
    color = await _servicio(db_superuser, a.tenant_id, "Color", 45000)
    descontinuado = await _servicio(db_superuser, a.tenant_id, "Discontinuado", 1, activo=False)
    await _servicio(db_superuser, a.tenant_id, "Sin profesional", 500)
    solo_baja = await _servicio(db_superuser, a.tenant_id, "Solo profesional de baja", 600)
    await _servicio(db_superuser, b.tenant_id, "Solo de B", 777)
    for servicio in (corte, color, descontinuado):
        await _ofrecer(db_superuser, a.tenant_id, prof, servicio)
    await _ofrecer(db_superuser, a.tenant_id, prof_baja, solo_baja)

    texto = _textos(await bot.boton(a, ANA, flujo.CB_SERVICIOS))[0]

    assert "- Corte: $12.500 (30 min)" in texto and "- Color: $45.000 (30 min)" in texto
    assert "Discontinuado" not in texto and "Solo de B" not in texto
    assert "Sin profesional" not in texto and "Solo profesional de baja" not in texto


async def test_datos_del_local_y_contacto_salen_de_settings_del_propio_tenant(pool_bot, db_superuser, tenants):
    a, b = tenants
    bot = Bot(pool_bot)
    await _alta_completa(bot, a, ANA)
    await _alta_completa(bot, b, ANA)
    settings = {
        local.CLAVE_DIRECCION: "Av. Siempreviva 742",
        local.CLAVE_HORARIOS: ["Lunes a viernes de 9 a 19", "Sábados de 9 a 13"],
        local.CLAVE_CONTACTO: "Tel. 11 5555-0000",
    }
    await db_superuser.execute(
        "update public.tenants set settings = $2::jsonb where id = $1", a.tenant_id, json.dumps(settings)
    )

    texto = _textos(await bot.boton(a, ANA, flujo.CB_LOCAL))[0]
    assert "Dirección: Av. Siempreviva 742" in texto
    assert "Horarios:\nLunes a viernes de 9 a 19" in texto and "Contacto: Tel. 11 5555-0000" in texto
    assert "Tel. 11 5555-0000" in _textos(await bot.boton(a, ANA, flujo.CB_NEGOCIO))[0]
    # cancelar o reprogramar: el bot no lo hace, muestra el contacto
    assert "Tel. 11 5555-0000" in _textos(await bot.texto(a, ANA, "quiero cancelar mi turno"))[0]

    # el otro tenant no cargó nada: el bot lo dice sin romper y no ve los datos de A
    texto_b = _textos(await bot.boton(b, ANA, flujo.CB_LOCAL))[0]
    assert flujo.TEXTO_SIN_DIRECCION in texto_b and "Siempreviva" not in texto_b
    assert _textos(await bot.boton(b, ANA, flujo.CB_NEGOCIO)) == [flujo.TEXTO_NEGOCIO_SIN_CONTACTO]


async def test_un_mensaje_fuera_de_menu_responde_con_el_menu_sin_romper_la_sesion(pool_bot, db_superuser, tenants):
    a, _ = tenants
    bot = Bot(pool_bot)
    await _alta_completa(bot, a, ANA)

    llamadas = await bot.texto(a, ANA, "asdf qwerty")

    assert _textos(llamadas) == [flujo.TEXTO_MENU]
    botones = llamadas[0]["cuerpo"]["reply_markup"]["inline_keyboard"]
    assert [b["callback_data"] for fila in botones for b in fila] == [
        flujo.CB_RESERVAR,
        flujo.CB_TURNOS,
        flujo.CB_SERVICIOS,
        flujo.CB_LOCAL,
        flujo.CB_NEGOCIO,
    ]
    # la sesión sigue sirviendo: el cliente ya identificado no vuelve a pasar por el alta
    assert _textos(await bot.boton(a, ANA, flujo.CB_SERVICIOS)) != [flujo.TEXTO_MENU]
    assert await _fila(db_superuser, a.tenant_id, ANA) is not None


async def test_la_consulta_de_un_cliente_sin_alta_completa_pide_el_dato_que_falta(pool_bot, tenants):
    a, _ = tenants
    bot = Bot(pool_bot)
    textos = _textos(await bot.boton(a, "2003", flujo.CB_TURNOS))
    assert textos == [base.flujo.TEXTO_PEDIR_TELEFONO]
