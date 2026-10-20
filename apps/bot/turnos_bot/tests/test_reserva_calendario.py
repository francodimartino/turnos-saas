"""Sincronización con Google Calendar después de reservar: nunca hace fallar la reserva (§7.1).

Conexión, pool y servicio de calendario simulados; la prueba contra Postgres real va en
`test_reserva_integracion.py`.
"""

import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from platform_calendar import (
    AccionSync,
    ErrorApiCalendar,
    ErrorConfiguracionCalendar,
    ResultadoSync,
)

from turnos_bot.despachador import Recursos
from turnos_bot.reserva import calendario

UTC = timezone.utc  # noqa: UP017
TENANT = uuid.uuid4()
BOOKING = uuid.uuid4()
PROFESIONAL = uuid.uuid4()
INICIO = datetime(2026, 10, 6, 14, 0, tzinfo=UTC)


class ConnFalsa:
    def __init__(self, fila) -> None:
        self.fila = fila
        self.ejecutados: list[tuple] = []

    async def fetchrow(self, sql, *args):
        return self.fila

    async def execute(self, sql, *args):
        self.ejecutados.append((" ".join(sql.split()), args))
        return "UPDATE 1"


def fila(status="confirmed", evento=None):
    return {
        "id": BOOKING,
        "status": status,
        "professional_id": PROFESIONAL,
        "google_calendar_event_id": evento,
        "inicio": INICIO,
        "fin": INICIO + timedelta(minutes=30),
        "servicio": "Corte",
        "cliente": "Ana Prueba",
        "telefono": "+5491100000000",
    }


class CalendarioFalso:
    def __init__(self, resultado=None, error=None) -> None:
        self.resultado, self.error = resultado, error
        self.pedidos: list = []

    async def sincronizar_booking(self, booking):
        self.pedidos.append(booking)
        if self.error:
            raise self.error
        return self.resultado


@pytest.fixture
def montar(monkeypatch):
    def _montar(conn: ConnFalsa):
        @asynccontextmanager
        async def tenant_tx(pool, tenant_id):
            yield conn

        monkeypatch.setattr(calendario, "tenant_tx", tenant_tx)

    return _montar


def recursos(fabrica) -> Recursos:
    return Recursos(
        pool=object(),
        cliente_http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
        fabrica_calendario=fabrica,
    )


async def test_crea_el_evento_y_guarda_el_id_en_el_booking(montar):
    conn = ConnFalsa(fila())
    montar(conn)
    servicio = CalendarioFalso(ResultadoSync(AccionSync.CREADO, "evt-123"))
    await calendario.sincronizar_reserva(TENANT, BOOKING)(recursos(lambda c, h: servicio))
    (pedido,) = servicio.pedidos
    assert (pedido.id, pedido.status, pedido.professional_id) == (BOOKING, "confirmed", PROFESIONAL)
    assert (pedido.servicio, pedido.cliente_nombre, pedido.inicio) == ("Corte", "Ana Prueba", INICIO)
    ((sql, args),) = conn.ejecutados
    assert "update public.bookings set google_calendar_event_id" in sql
    assert args == (BOOKING, TENANT, "evt-123")


@pytest.mark.parametrize("accion", [AccionSync.SIN_CONEXION, AccionSync.IGNORADO])
async def test_si_el_profesional_no_tiene_integracion_no_toca_la_reserva(montar, accion):
    conn = ConnFalsa(fila())
    montar(conn)
    servicio = CalendarioFalso(ResultadoSync(accion, None))
    await calendario.sincronizar_reserva(TENANT, BOOKING)(recursos(lambda c, h: servicio))
    assert conn.ejecutados == []


async def test_un_booking_que_ya_no_esta_confirmed_nunca_va_al_calendario(montar):
    conn = ConnFalsa(fila(status="pending"))
    montar(conn)
    servicio = CalendarioFalso(ResultadoSync(AccionSync.CREADO, "evt-x"))
    await calendario.sincronizar_reserva(TENANT, BOOKING)(recursos(lambda c, h: servicio))
    assert servicio.pedidos == [] and conn.ejecutados == []


async def test_un_booking_que_ya_tiene_evento_no_se_duplica(montar):
    conn = ConnFalsa(fila(evento="evt-previo"))
    montar(conn)
    servicio = CalendarioFalso(ResultadoSync(AccionSync.CREADO, "evt-nuevo"))
    await calendario.sincronizar_reserva(TENANT, BOOKING)(recursos(lambda c, h: servicio))
    assert servicio.pedidos == []


async def test_calendar_no_configurado_no_falla(montar, caplog):
    conn = ConnFalsa(fila())
    montar(conn)

    def fabrica(c, h):
        raise ErrorConfiguracionCalendar("faltan variables")

    with caplog.at_level(logging.WARNING):
        await calendario.sincronizar_reserva(TENANT, BOOKING)(recursos(fabrica))
    assert conn.ejecutados == []
    assert "calendario_no_configurado" in caplog.text


async def test_un_fallo_de_google_no_se_propaga_ni_guarda_nada(montar, caplog):
    conn = ConnFalsa(fila())
    montar(conn)
    servicio = CalendarioFalso(error=ErrorApiCalendar(503))
    with caplog.at_level(logging.WARNING):
        await calendario.sincronizar_reserva(TENANT, BOOKING)(recursos(lambda c, h: servicio))
    assert conn.ejecutados == []
    assert "calendar_sync_fallido" in caplog.text
    assert "Ana Prueba" not in caplog.text and "+5491100000000" not in caplog.text
