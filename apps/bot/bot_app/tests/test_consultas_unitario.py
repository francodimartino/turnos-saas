"""Consultas del cliente (turnos, servicios, local, negocio) y menú, con una conexión en memoria.

El aislamiento real por tenant y por cliente se prueba contra Postgres en `test_consultas_integracion.py`.
Nombres, teléfonos y direcciones son inventados.
"""

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from platform_notifications import MensajeEntrante

from bot_app import ContextoMensaje
from bot_app.consultas import flujo, local
from bot_app.consultas.flujo import manejar_consultas, mostrar_menu_principal
from bot_app.flujos import handlers_por_defecto
from bot_app.identidad.repositorio import EndCustomer

UTC = timezone.utc  # noqa: UP017
TENANT = uuid.uuid4()
CLIENTE = EndCustomer(uuid.uuid4(), TENANT, "telegram", "111", "+5491100000000", "Ana Prueba")
AHORA = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


class ConnFalsa:
    def __init__(self, turnos=(), servicios=(), tenant=None) -> None:
        self.turnos, self.servicios, self.tenant = list(turnos), list(servicios), tenant
        self.consultas: list[tuple[str, tuple]] = []

    async def fetch(self, sql, *args):
        self.consultas.append((sql, args))
        if "from public.bookings" in sql:
            return self.turnos
        if "from public.services" in sql:
            return self.servicios
        raise AssertionError(sql)

    async def fetchrow(self, sql, *args):
        self.consultas.append((sql, args))
        assert "from public.tenants" in sql
        return self.tenant


def msg(texto=None, callback=None) -> MensajeEntrante:
    return MensajeEntrante("telegram", "111", "111", "1", texto=texto, callback_data=callback)


def ctx_de(conn, texto=None, callback=None, sesion=None) -> ContextoMensaje:
    return ContextoMensaje(conn, TENANT, msg(texto, callback), dict(sesion or {}))


@pytest.fixture
def cliente_identificado(monkeypatch):
    async def _obtener(ctx):
        return CLIENTE

    monkeypatch.setattr(flujo, "obtener_o_crear_end_customer", _obtener)


def _tenant(settings, nombre="Estética Prueba"):
    return {"name": nombre, "settings": settings}


def _dato_botones(ctx) -> list[str]:
    return [b.dato for b in ctx.respuestas[-1].botones]


async def test_mis_turnos_lista_servicio_profesional_y_horario_en_hora_argentina(cliente_identificado):
    # 2026-10-06 18:30 UTC = martes 15:30 en Buenos Aires
    turno = {
        "inicio": datetime(2026, 10, 6, 18, 30, tzinfo=UTC),
        "servicio": "Corte",
        "profesional": "Lucía",
        "status": "confirmed",
    }
    pendiente = {**turno, "inicio": datetime(2026, 10, 8, 12, 0, tzinfo=UTC), "status": "pending"}
    conn = ConnFalsa(turnos=[turno, pendiente])
    ctx = ctx_de(conn, callback=flujo.CB_TURNOS)

    assert await manejar_consultas(ctx, ahora=AHORA) is True

    texto = ctx.respuestas[0].texto
    assert "martes 6/10 a las 15:30: Corte con Lucía" in texto
    assert "jueves 8/10 a las 09:00: Corte con Lucía (pendiente de confirmación)" in texto
    # el filtro va en la consulta: tenant, cliente propio y solo futuros
    sql, args = conn.consultas[0]
    assert args[:3] == (TENANT, CLIENTE.id, AHORA)
    assert "'cancelled'" not in sql and "status in ('pending', 'confirmed')" in sql


async def test_mis_turnos_sin_turnos_lo_dice(cliente_identificado):
    ctx = ctx_de(ConnFalsa(), texto="Mis turnos")
    assert await manejar_consultas(ctx, ahora=AHORA) is True
    assert ctx.respuestas[0].texto == flujo.TEXTO_SIN_TURNOS


async def test_mis_turnos_con_alta_en_curso_corta_sin_consultar(monkeypatch):
    async def _sin_alta(ctx):
        ctx.responder("necesito tu teléfono")
        return None

    monkeypatch.setattr(flujo, "obtener_o_crear_end_customer", _sin_alta)
    conn = ConnFalsa()
    ctx = ctx_de(conn, callback=flujo.CB_TURNOS)

    assert await manejar_consultas(ctx, ahora=AHORA) is True
    assert [r.texto for r in ctx.respuestas] == ["necesito tu teléfono"]
    assert conn.consultas == []


async def test_servicios_y_precios_formatea_el_precio_en_pesos():
    servicios = [
        {"name": "Corte", "price": Decimal("12500"), "duration_minutes": 30},
        {"name": "Color", "price": Decimal("45000.50"), "duration_minutes": 90},
    ]
    conn = ConnFalsa(servicios=servicios)
    ctx = ctx_de(conn, callback=flujo.CB_SERVICIOS)

    assert await manejar_consultas(ctx) is True

    assert "- Corte: $12.500 (30 min)" in ctx.respuestas[0].texto
    assert "- Color: $45.000,50 (90 min)" in ctx.respuestas[0].texto
    sql, args = conn.consultas[0]
    assert "is_active" in sql and args[0] == TENANT


async def test_servicios_sin_cargar_lo_dice_con_amabilidad():
    ctx = ctx_de(ConnFalsa(), texto="precios")
    assert await manejar_consultas(ctx) is True
    assert ctx.respuestas[0].texto == flujo.TEXTO_SIN_SERVICIOS


async def test_datos_del_local_muestra_direccion_horarios_y_contacto():
    settings = {
        local.CLAVE_DIRECCION: "Av. Siempreviva 742",
        local.CLAVE_HORARIOS: ["Lunes a viernes de 9 a 19", "Sábados de 9 a 13"],
        local.CLAVE_CONTACTO: "WhatsApp +54 11 5555-0000",
    }
    ctx = ctx_de(ConnFalsa(tenant=_tenant(settings)), callback=flujo.CB_LOCAL)

    assert await manejar_consultas(ctx) is True

    texto = ctx.respuestas[0].texto
    assert "Datos de Estética Prueba:" in texto
    assert "Dirección: Av. Siempreviva 742" in texto
    assert "Horarios:\nLunes a viernes de 9 a 19\nSábados de 9 a 13" in texto
    assert "Contacto: WhatsApp +54 11 5555-0000" in texto


async def test_datos_del_local_acepta_settings_como_texto_json_y_horario_en_una_linea():
    settings = json.dumps({local.CLAVE_HORARIOS: "Todos los días de 10 a 20"})
    ctx = ctx_de(ConnFalsa(tenant=_tenant(settings)), callback=flujo.CB_LOCAL)
    await manejar_consultas(ctx)
    assert "Horarios: Todos los días de 10 a 20" in ctx.respuestas[0].texto


@pytest.mark.parametrize("settings", [{}, {local.CLAVE_DIRECCION: 5, local.CLAVE_CONTACTO: "   ", "otra": "x"}, None])
async def test_datos_del_local_faltantes_se_avisan_sin_romper(settings):
    ctx = ctx_de(ConnFalsa(tenant=_tenant(settings)), callback=flujo.CB_LOCAL)
    assert await manejar_consultas(ctx) is True
    texto = ctx.respuestas[0].texto
    for faltante in (flujo.TEXTO_SIN_DIRECCION, flujo.TEXTO_SIN_HORARIOS, flujo.TEXTO_SIN_CONTACTO):
        assert faltante in texto


async def test_datos_del_local_con_tenant_no_visible_no_rompe():
    ctx = ctx_de(ConnFalsa(tenant=None), callback=flujo.CB_LOCAL)
    assert await manejar_consultas(ctx) is True
    assert flujo.TEXTO_SIN_DIRECCION in ctx.respuestas[0].texto


async def test_datos_del_local_recorta_textos_largos_y_limita_las_lineas_de_horarios():
    settings = {
        local.CLAVE_DIRECCION: "x" * 1000,
        local.CLAVE_HORARIOS: [f"Día {i}" for i in range(50)],
    }
    datos = await local.leer_datos_local(ConnFalsa(tenant=_tenant(settings)), TENANT)
    assert len(datos.direccion) == local.LARGO_MAXIMO
    assert len(datos.horarios.splitlines()) == local.LINEAS_MAXIMAS


async def test_hablar_con_el_negocio_muestra_el_contacto():
    settings = {local.CLAVE_CONTACTO: "Tel. 11 5555-0000"}
    ctx = ctx_de(ConnFalsa(tenant=_tenant(settings)), callback=flujo.CB_NEGOCIO)
    assert await manejar_consultas(ctx) is True
    assert "Tel. 11 5555-0000" in ctx.respuestas[0].texto


@pytest.mark.parametrize("pedido", ["quiero cancelar mi turno", "Necesito REPROGRAMAR", "cancelar", "contacto"])
async def test_pedidos_de_cancelar_o_reprogramar_se_derivan_al_contacto_del_local(pedido):
    settings = {local.CLAVE_CONTACTO: "Tel. 11 5555-0000"}
    conn = ConnFalsa(tenant=_tenant(settings))
    ctx = ctx_de(conn, texto=pedido)

    assert await manejar_consultas(ctx) is True

    assert "Tel. 11 5555-0000" in ctx.respuestas[0].texto
    assert all("update" not in s.lower() and "insert" not in s.lower() for s, _ in conn.consultas)


async def test_hablar_con_el_negocio_sin_contacto_cargado_lo_dice():
    ctx = ctx_de(ConnFalsa(tenant=_tenant({})), callback=flujo.CB_NEGOCIO)
    assert await manejar_consultas(ctx) is True
    assert ctx.respuestas[0].texto == flujo.TEXTO_NEGOCIO_SIN_CONTACTO


async def test_el_menu_tiene_los_cinco_botones_incluido_reservar():
    ctx = ctx_de(ConnFalsa())
    await mostrar_menu_principal(ctx)
    assert _dato_botones(ctx) == ["reservar", "con:turnos", "con:servicios", "con:local", "con:negocio"]


async def test_cada_respuesta_de_consulta_deja_el_menu_a_mano():
    ctx = ctx_de(ConnFalsa(), callback=flujo.CB_SERVICIOS)
    await manejar_consultas(ctx)
    assert _dato_botones(ctx)[0] == flujo.CB_RESERVAR


async def test_un_boton_con_viejo_o_el_pedido_de_menu_vuelven_al_menu():
    for dato in (flujo.CB_MENU, "con:inexistente"):
        ctx = ctx_de(ConnFalsa(), callback=dato)
        assert await manejar_consultas(ctx) is True
        assert ctx.respuestas[0].texto == flujo.TEXTO_MENU


@pytest.mark.parametrize("texto", ["quiero un turno mañana", "hola", "", None])
async def test_un_mensaje_fuera_de_menu_no_lo_atiende_el_handler_y_no_toca_la_sesion(texto):
    ctx = ctx_de(ConnFalsa(), texto=texto)
    assert await manejar_consultas(ctx) is False
    assert ctx.sesion == {} and ctx.respuestas == []


async def test_el_boton_de_reservar_y_otros_flujos_no_son_de_consultas():
    for dato in ("reservar", "idn:borrar_si", "res:servicio:1"):
        ctx = ctx_de(ConnFalsa(), callback=dato)
        assert await manejar_consultas(ctx) is False


async def test_con_otro_flujo_en_curso_el_texto_no_se_interpreta_como_consulta():
    # "cancelar" en medio de la reserva es de la reserva, no un pedido de contacto
    ctx = ctx_de(ConnFalsa(), texto="cancelar", sesion={"reserva": {"paso": "servicio"}})
    assert await manejar_consultas(ctx) is False
    # pero un botón de consulta sí funciona y no pisa la sesión
    ctx = ctx_de(ConnFalsa(), callback=flujo.CB_SERVICIOS, sesion={"reserva": {"paso": "servicio"}})
    assert await manejar_consultas(ctx) is True
    assert ctx.sesion == {"reserva": {"paso": "servicio"}}


async def test_ningun_texto_expone_ids_internos(cliente_identificado):
    turno = {"inicio": datetime(2026, 10, 6, 18, 30, tzinfo=UTC), "servicio": "Corte", "profesional": "Lucía",
             "status": "confirmed"}  # fmt: skip
    conn = ConnFalsa(
        turnos=[turno],
        servicios=[{"name": "Corte", "price": Decimal(100), "duration_minutes": 30}],
        tenant=_tenant({local.CLAVE_CONTACTO: "Tel. 11 5555-0000"}),
    )
    for dato in (flujo.CB_TURNOS, flujo.CB_SERVICIOS, flujo.CB_LOCAL, flujo.CB_NEGOCIO, flujo.CB_MENU):
        ctx = ctx_de(conn, callback=dato)
        await manejar_consultas(ctx, ahora=AHORA)
        for r in ctx.respuestas:
            assert str(CLIENTE.id) not in r.texto and str(TENANT) not in r.texto
            assert not any(c in r.texto for c in ("uuid", "tenant_id"))


def test_consultas_va_despues_de_identidad_en_la_cadena_de_produccion():
    nombres = [h.__name__ for h in handlers_por_defecto()]
    assert nombres.index("manejar_identidad") < nombres.index("manejar_consultas")
