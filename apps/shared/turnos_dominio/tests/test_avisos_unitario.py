"""Aviso al cliente cuyo turno pendiente vencido se libera: encolado en el outbox y pedido de envío."""

import json
import uuid
from datetime import datetime, timezone

import pytest
from turnos_negocio import PLANTILLA_VENCIMIENTO, mensaje_vencimiento

from turnos_dominio import alta, avisos
from turnos_dominio.alta import Liberado

UTC = timezone.utc  # noqa: UP017
TENANT = uuid.uuid4()
INICIO = datetime(2026, 10, 21, 15, 30, tzinfo=UTC)


def liberado(**cambios) -> Liberado:
    datos = {
        "tenant_id": TENANT,
        "booking_id": uuid.uuid4(),
        "channel": "telegram",
        "channel_user_id": "222",
        "notify": True,
        "end_customer_id": uuid.uuid4(),
        "starts_at": INICIO,
        "service_name": "Corte",
    }
    return Liberado(**{**datos, **cambios})


class ConnOutbox:
    """Conexión simulada: `enqueue_notification` devuelve un id, o `None` para un cliente anonimizado."""

    def __init__(self, anonimizados: frozenset[uuid.UUID] = frozenset()) -> None:
        self.anonimizados = anonimizados
        self.llamadas: list[tuple] = []

    async def fetchval(self, sql, *args):
        assert "enqueue_notification" in sql
        self.llamadas.append(args)
        return None if args[0] in self.anonimizados else uuid.uuid4()


async def test_encola_un_aviso_por_liberado_con_plantilla_y_params_sin_pii():
    lib = liberado()
    conn = ConnOutbox()
    (aviso,) = await avisos.encolar_avisos_liberados(conn, [lib])
    ((cliente, canal, plantilla, params, _),) = conn.llamadas
    assert (cliente, canal, plantilla) == (lib.end_customer_id, "telegram", PLANTILLA_VENCIMIENTO)
    assert plantilla == "turno_vencido"
    assert json.loads(params) == {"booking_id": str(lib.booking_id), "inicio": INICIO.isoformat()}
    assert aviso.liberado is lib and aviso.outbox_id is not None


@pytest.mark.parametrize(
    "cambios",
    [
        {"notify": False},
        {"channel": None},
        {"channel_user_id": None},
        {"end_customer_id": None},
        {"starts_at": None},
        {"service_name": None},
    ],
)
async def test_sin_a_quien_avisar_no_se_encola_nada(cambios):
    conn = ConnOutbox()
    assert await avisos.encolar_avisos_liberados(conn, [liberado(**cambios)]) == ()
    assert conn.llamadas == []


async def test_un_cliente_anonimizado_no_recibe_aviso():
    lib = liberado()
    conn = ConnOutbox(anonimizados=frozenset({lib.end_customer_id}))
    assert await avisos.encolar_avisos_liberados(conn, [lib]) == ()
    assert len(conn.llamadas) == 1


async def test_el_aviso_de_uno_no_depende_de_los_otros():
    uno, otro = liberado(), liberado(notify=False)
    avisados = await avisos.encolar_avisos_liberados(ConnOutbox(), [otro, uno])
    assert [a.liberado for a in avisados] == [uno]


def test_el_pedido_de_envio_usa_el_texto_compartido_y_cierra_la_fila_del_outbox():
    lib = liberado()
    aviso = avisos.AvisoLiberado(lib, uuid.uuid4())
    pedido = avisos.pedido_de_envio(aviso)
    assert pedido.texto == mensaje_vencimiento(INICIO, "Corte")
    assert pedido.template == PLANTILLA_VENCIMIENTO
    assert pedido.destinatario == "222" and pedido.end_customer_id == lib.end_customer_id
    assert pedido.outbox_id == aviso.outbox_id


class ConnLiberar:
    def __init__(self, filas) -> None:
        self.filas, self.args = filas, None

    async def fetch(self, sql, *args):
        self.args = (sql, args)
        return self.filas


async def test_liberar_vencidos_solapados_devuelve_los_liberados():
    fila = {
        "tenant_id": TENANT,
        "booking_id": uuid.uuid4(),
        "channel": "telegram",
        "channel_user_id": "222",
        "notify": True,
        "end_customer_id": uuid.uuid4(),
        "starts_at": INICIO,
        "service_name": "Corte",
    }
    conn = ConnLiberar([fila])
    profesional = uuid.uuid4()
    fin = datetime(2026, 10, 21, 16, 0, tzinfo=UTC)
    (lib,) = await alta.liberar_vencidos_solapados(conn, profesional_id=profesional, inicio=INICIO, fin=fin)
    assert lib.booking_id == fila["booking_id"] and lib.notify
    assert conn.args == (alta.SQL_LIBERAR_VENCIDOS, (profesional, INICIO, fin))
