"""Sesión de conversación tipada y versionada: lectura, descarte por versión o forma, migración del formato anterior."""

import json
import logging
import uuid

import pytest
from platform_tenancy.testing import TransaccionPorExecute

from turnos_bot.modelo_sesion import (
    VERSION_CAMBIO,
    VERSION_IDENTIDAD,
    VERSION_RESERVA,
    VERSION_SESION,
    SesionBot,
    SesionCambio,
    SesionIdentidad,
    SesionInvalida,
    SesionReserva,
)
from turnos_bot.sesiones import guardar_sesion, leer_sesion
from turnos_bot.tests.conftest import update
from turnos_bot.tests.test_webhook_unitario import enviar, montar

TENANT = uuid.uuid4()
SERVICIO = uuid.uuid4()
PROFESIONAL = uuid.uuid4()


class ConnSesion(TransaccionPorExecute):
    """Una sola fila de `bot_sessions` en memoria."""

    def __init__(self, estado=None) -> None:
        self.estado = estado
        self.borrados = 0

    async def execute(self, sql: str, *args):
        if sql.startswith("delete") and "updated_at" not in sql:
            self.estado = None
            self.borrados += 1
        elif sql.startswith("insert"):
            self.estado = args[3]

    async def fetchrow(self, sql: str, *args):
        return None if self.estado is None else {"state": self.estado}


def completa() -> SesionBot:
    reserva = SesionReserva("confirmar", SERVICIO, PROFESIONAL, 1_800_000_000, 2)
    return SesionBot(reserva=reserva, identidad=SesionIdentidad("nombre"))


async def test_una_sesion_valida_se_guarda_y_se_lee_sin_perdida():
    conn = ConnSesion()
    await guardar_sesion(conn, TENANT, "telegram", "1", completa())
    assert await leer_sesion(conn, TENANT, "telegram", "1") == completa()


async def test_el_json_guardado_lleva_las_versiones():
    conn = ConnSesion()
    await guardar_sesion(conn, TENANT, "telegram", "1", completa())
    guardado = json.loads(conn.estado)
    assert guardado["version"] == VERSION_SESION and guardado["reserva"]["version"] == VERSION_RESERVA


async def test_una_sesion_con_version_distinta_se_descarta_y_se_borra(caplog):
    conn = ConnSesion(json.dumps({**completa().a_json(), "version": VERSION_SESION + 1}))
    with caplog.at_level(logging.WARNING):
        assert await leer_sesion(conn, TENANT, "telegram", "1") is None
    assert conn.estado is None and conn.borrados == 1
    assert str(SERVICIO) not in caplog.text and "nombre" not in caplog.text


async def test_una_reserva_con_version_distinta_descarta_toda_la_sesion():
    datos = completa().a_json()
    datos["reserva"]["version"] = VERSION_RESERVA + 1
    conn = ConnSesion(json.dumps(datos))
    assert await leer_sesion(conn, TENANT, "telegram", "1") is None


@pytest.mark.parametrize(
    "estado",
    [
        [],
        {"version": VERSION_SESION, "otro_flujo": {}},
        {"version": VERSION_SESION, "reserva": {"paso": "inexistente"}},
        {
            "version": VERSION_SESION,
            "reserva": {"paso": "horario", "servicio_id": str(SERVICIO)},
        },  # falta el profesional
        {"version": VERSION_SESION, "reserva": {"paso": "servicio", "servicio_id": "no-es-uuid"}},
        {"version": VERSION_SESION, "reserva": {"paso": "servicio", "pagina": "3"}},
        {
            "version": VERSION_SESION,
            "reserva": {"paso": "confirmar", "servicio_id": str(SERVICIO), "profesional_id": str(PROFESIONAL)},
        },
        {"version": VERSION_SESION, "identidad": {"paso": 3}},
        {"version": VERSION_SESION, "identidad": {"paso": "inexistente"}},
        {"version": VERSION_SESION, "identidad": {"paso": "recontacto"}},  # falta el perfil anterior
        {"version": VERSION_SESION, "identidad": {"paso": "recontacto", "dormido_id": "no-es-uuid"}},
        {"version": VERSION_SESION, "identidad": {"paso": "nombre", "otra": "x"}},
        {"version": True},
    ],
)
async def test_una_sesion_con_forma_invalida_se_descarta(estado):
    conn = ConnSesion(json.dumps(estado))
    assert await leer_sesion(conn, TENANT, "telegram", "1") is None
    assert conn.estado is None


async def test_una_sesion_en_el_formato_anterior_sin_versiones_se_migra():
    anterior = {
        "reserva": {"paso": "horario", "servicio_id": str(SERVICIO), "profesional_id": str(PROFESIONAL), "pagina": 1},
        "identidad": {"paso": "nombre"},
    }
    conn = ConnSesion(json.dumps(anterior))
    sesion = await leer_sesion(conn, TENANT, "telegram", "1")
    assert sesion == SesionBot(
        reserva=SesionReserva("horario", SERVICIO, PROFESIONAL, None, 1), identidad=SesionIdentidad("nombre")
    )
    # Al guardarla de nuevo queda en el formato actual.
    await guardar_sesion(conn, TENANT, "telegram", "1", sesion)
    assert json.loads(conn.estado)["version"] == VERSION_SESION


def test_desde_json_levanta_sesion_invalida():
    with pytest.raises(SesionInvalida):
        SesionBot.desde_json({"version": 0})


async def test_el_cliente_con_una_sesion_vieja_vuelve_al_menu(pool, datos_bot):
    pool.sesiones["42"] = {"version": VERSION_SESION + 1, "reserva": {"version": VERSION_RESERVA, "paso": "servicio"}}
    vistas = []

    async def handler(ctx):
        vistas.append(bool(ctx.sesion))
        ctx.responder("menu")
        return True

    cliente, _ = montar(pool, [handler])
    r = await enviar(cliente, datos_bot, update(1, usuario=42))
    assert r.status_code == 200 and vistas == [False]
    assert "42" not in pool.sesiones


DORMIDO = uuid.uuid4()


async def test_identidad_en_recontacto_se_guarda_se_lee_y_continua():
    conn = ConnSesion()
    sesion = SesionBot(identidad=SesionIdentidad("recontacto", DORMIDO))
    await guardar_sesion(conn, TENANT, "telegram", "1", sesion)
    assert json.loads(conn.estado)["identidad"] == {
        "version": VERSION_IDENTIDAD,
        "paso": "recontacto",
        "dormido_id": str(DORMIDO),
    }
    leida = await leer_sesion(conn, TENANT, "telegram", "1")
    assert leida == sesion and leida.identidad.dormido == DORMIDO


async def test_una_identidad_con_version_distinta_descarta_la_sesion():
    datos = SesionBot(identidad=SesionIdentidad("telefono")).a_json()
    datos["identidad"]["version"] = VERSION_IDENTIDAD + 1
    conn = ConnSesion(json.dumps(datos))
    assert await leer_sesion(conn, TENANT, "telegram", "1") is None
    assert conn.estado is None


async def test_una_identidad_en_el_formato_anterior_se_migra_con_su_perfil_anterior():
    anterior = {"identidad": {"paso": "recontacto", "dormido_id": str(DORMIDO)}}
    sesion = await leer_sesion(ConnSesion(json.dumps(anterior)), TENANT, "telegram", "1")
    assert sesion == SesionBot(identidad=SesionIdentidad("recontacto", DORMIDO))


def test_las_consultas_no_guardan_estado_en_la_sesion():
    assert not SesionBot() and SesionBot().a_json() == {"version": VERSION_SESION}


TURNO = uuid.uuid4()


async def test_el_cambio_de_turno_se_guarda_se_lee_y_continua():
    conn = ConnSesion()
    sesion = SesionBot(cambio=SesionCambio("confirmar", TURNO, 1_800_000_000, 2))
    await guardar_sesion(conn, TENANT, "telegram", "1", sesion)
    assert json.loads(conn.estado)["cambio"] == {
        "version": VERSION_CAMBIO,
        "paso": "confirmar",
        "booking_id": str(TURNO),
        "inicio": 1_800_000_000,
        "pagina": 2,
    }
    leida = await leer_sesion(conn, TENANT, "telegram", "1")
    assert leida == sesion and leida.cambio.turno == TURNO and leida.cambio.horario == 1_800_000_000
    assert bool(leida)


async def test_un_cambio_con_version_distinta_descarta_la_sesion():
    datos = SesionBot(cambio=SesionCambio("horario", TURNO)).a_json()
    datos["cambio"]["version"] = VERSION_CAMBIO + 1
    conn = ConnSesion(json.dumps(datos))
    assert await leer_sesion(conn, TENANT, "telegram", "1") is None
    assert conn.estado is None


async def test_una_sesion_de_la_version_anterior_al_cambio_de_turno_se_descarta():
    anterior = {"version": VERSION_SESION - 1, "reserva": {"version": VERSION_RESERVA, "paso": "servicio"}}
    conn = ConnSesion(json.dumps(anterior))
    assert VERSION_SESION >= 2
    assert await leer_sesion(conn, TENANT, "telegram", "1") is None
    assert conn.estado is None and conn.borrados == 1


async def test_una_sesion_guardada_antes_de_las_versiones_sigue_migrando_con_el_cambio_vacio():
    sesion = await leer_sesion(ConnSesion(json.dumps({"identidad": {"paso": "nombre"}})), TENANT, "telegram", "1")
    assert sesion == SesionBot(identidad=SesionIdentidad("nombre")) and sesion.cambio is None


@pytest.mark.parametrize(
    "cambio",
    [
        {"paso": "inexistente", "booking_id": str(TURNO)},
        {"paso": "horario"},  # falta el turno
        {"paso": "horario", "booking_id": "no-es-uuid"},
        {"paso": "horario", "booking_id": 7},
        {"paso": "confirmar", "booking_id": str(TURNO)},  # falta el horario elegido
        {"paso": "horario", "booking_id": str(TURNO), "pagina": -1},
        {"paso": "horario", "booking_id": str(TURNO), "pagina": "1"},
        {"paso": "horario", "booking_id": str(TURNO), "inicio": "x"},
        {"paso": "horario", "booking_id": str(TURNO), "otra": 1},
        [],
    ],
)
async def test_un_cambio_con_forma_invalida_se_descarta(cambio):
    conn = ConnSesion(json.dumps({"version": VERSION_SESION, "cambio": cambio}))
    assert await leer_sesion(conn, TENANT, "telegram", "1") is None
    assert conn.estado is None


def test_clear_y_copia_incluyen_el_cambio():
    sesion = SesionBot(cambio=SesionCambio("horario", TURNO))
    copia = sesion.copia()
    sesion.clear()
    assert not sesion and sesion.cambio is None and copia.cambio is not None
