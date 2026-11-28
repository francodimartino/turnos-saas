"""Calendar al cancelar o mover un turno: después del commit, y nunca hace fallar el cambio (§7.1).

Conexión, pool y servicio de calendario simulados; la prueba contra Postgres real va en
`test_cambios_integracion.py`. Las filas llevan lo que da la consulta real (`anonimizado` es un booleano).
"""

import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from platform_calendar import AccionSync, ErrorApiCalendar, ErrorConfiguracionCalendar, ResultadoSync
from platform_tenancy.testing import TransaccionPorExecute

from turnos_bot.cambios import calendario
from turnos_bot.despachador import Recursos

UTC = timezone.utc  # noqa: UP017
TENANT = uuid.uuid4()
BOOKING = uuid.uuid4()
PROFESIONAL = uuid.uuid4()
INICIO = datetime(2026, 10, 6, 14, 0, tzinfo=UTC)


class ConnFalsa(TransaccionPorExecute):
    def __init__(self, fila) -> None:
        self.fila = fila
        self.ejecutados: list[tuple] = []

    async def fetchrow(self, sql, *args):
        return self.fila

    async def execute(self, sql, *args):
        self.ejecutados.append((" ".join(sql.split()), args))
        return "UPDATE 1"


def fila(status="confirmed", evento=None, anonimizado=False):
    return {
        "id": BOOKING,
        "status": status,
        "professional_id": PROFESIONAL,
        "google_calendar_event_id": evento,
        "inicio": INICIO,
        "fin": INICIO + timedelta(minutes=30),
        "servicio": "Corte",
        "cliente": "Ana Prueba",
        "anonimizado": anonimizado,
    }


class CalendarioFalso:
    def __init__(self, resultado=None, error=None) -> None:
        self.resultado, self.error = resultado, error
        self.sincronizados: list = []
        self.retirados: list = []

    async def sincronizar_evento(self, profesional_id, evento):
        self.sincronizados.append((profesional_id, evento))
        if self.error:
            raise self.error
        return self.resultado

    async def retirar_evento(self, profesional_id, id_externo):
        self.retirados.append((profesional_id, id_externo))
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


async def correr(servicio) -> None:
    await calendario.sincronizar_cambio(TENANT, BOOKING)(recursos(lambda c, h: servicio))


async def test_un_turno_cancelado_borra_el_evento_y_limpia_su_id(montar):
    conn = ConnFalsa(fila("cancelled", evento="evt-1"))
    montar(conn)
    servicio = CalendarioFalso(ResultadoSync(AccionSync.BORRADO, None))
    await correr(servicio)
    assert servicio.retirados == [(PROFESIONAL, "evt-1")] and servicio.sincronizados == []
    ((sql, args),) = conn.ejecutados
    assert "update public.bookings set google_calendar_event_id = $3::text" in sql
    assert args == (BOOKING, TENANT, None)


async def test_un_turno_cancelado_sin_evento_no_toca_el_calendario(montar):
    conn = ConnFalsa(fila("cancelled"))
    montar(conn)
    servicio = CalendarioFalso(ResultadoSync(AccionSync.BORRADO, None))
    await correr(servicio)
    assert servicio.retirados == [] and conn.ejecutados == []


async def test_un_turno_movido_reescribe_el_evento_en_el_horario_nuevo_sin_telefono(montar):
    conn = ConnFalsa(fila("confirmed", evento="evt-1"))
    montar(conn)
    servicio = CalendarioFalso(ResultadoSync(AccionSync.ACTUALIZADO, "evt-1"))
    await correr(servicio)
    ((profesional, evento),) = servicio.sincronizados
    assert profesional == PROFESIONAL
    assert (evento.titulo, evento.inicio, evento.id_externo) == ("Corte — Ana Prueba", INICIO, "evt-1")
    assert conn.ejecutados == []  # el id no cambió: no se escribe


async def test_un_turno_confirmado_sin_evento_lo_crea_y_guarda_el_id(montar):
    conn = ConnFalsa(fila("confirmed"))
    montar(conn)
    await correr(CalendarioFalso(ResultadoSync(AccionSync.CREADO, "evt-nuevo")))
    ((_, args),) = conn.ejecutados
    assert args == (BOOKING, TENANT, "evt-nuevo")


async def test_un_cliente_anonimizado_no_deja_su_nombre_en_el_evento(montar):
    conn = ConnFalsa(fila("confirmed", evento="evt-1", anonimizado=True))
    montar(conn)
    servicio = CalendarioFalso(ResultadoSync(AccionSync.ACTUALIZADO, "evt-1"))
    await correr(servicio)
    ((_, evento),) = servicio.sincronizados
    assert "Ana Prueba" not in evento.titulo + evento.descripcion


async def test_un_turno_pendiente_nunca_va_al_calendario(montar):
    conn = ConnFalsa(fila("pending", evento="evt-1"))
    montar(conn)
    servicio = CalendarioFalso(ResultadoSync(AccionSync.ACTUALIZADO, "evt-1"))
    await correr(servicio)
    assert servicio.sincronizados == [] and servicio.retirados == [] and conn.ejecutados == []


async def test_un_turno_que_ya_no_existe_no_hace_nada(montar):
    conn = ConnFalsa(None)
    montar(conn)
    servicio = CalendarioFalso(ResultadoSync(AccionSync.CREADO, "evt-x"))
    await correr(servicio)
    assert servicio.sincronizados == [] and conn.ejecutados == []


@pytest.mark.parametrize("estado", ["cancelled", "confirmed"])
async def test_sin_calendario_conectado_conserva_el_id(montar, estado):
    conn = ConnFalsa(fila(estado, evento="evt-1"))
    montar(conn)
    await correr(CalendarioFalso(ResultadoSync(AccionSync.SIN_CONEXION, "evt-1")))
    assert conn.ejecutados == []


async def test_calendar_no_configurado_no_falla(montar, caplog):
    conn = ConnFalsa(fila("confirmed", evento="evt-1"))
    montar(conn)

    def fabrica(c, h):
        raise ErrorConfiguracionCalendar("faltan variables")

    with caplog.at_level(logging.WARNING):
        await calendario.sincronizar_cambio(TENANT, BOOKING)(recursos(fabrica))
    assert conn.ejecutados == [] and "calendario_no_configurado" in caplog.text


@pytest.mark.parametrize("estado", ["cancelled", "confirmed"])
async def test_un_fallo_de_google_no_se_propaga_ni_guarda_nada(montar, caplog, estado):
    conn = ConnFalsa(fila(estado, evento="evt-1"))
    montar(conn)
    with caplog.at_level(logging.WARNING):
        await correr(CalendarioFalso(error=ErrorApiCalendar(503)))
    assert conn.ejecutados == [] and "calendar_sync_fallido" in caplog.text
    assert "Ana Prueba" not in caplog.text
