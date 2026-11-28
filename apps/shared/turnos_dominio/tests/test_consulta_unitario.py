"""`calcular_huecos` con una conexión en memoria: qué parámetros le pasa a la consulta de ocupados."""

import uuid
from datetime import datetime, timezone

from turnos_dominio import Ventana
from turnos_dominio.consulta import _SQL_OCUPADOS, calcular_huecos

UTC = timezone.utc  # noqa: UP017
RANGO = Ventana(datetime(2026, 10, 5, 12, tzinfo=UTC), datetime(2026, 10, 5, 15, tzinfo=UTC))


class ConnFalsa:
    def __init__(self) -> None:
        self.pedidos: list[tuple] = []

    async def fetchval(self, sql, *args):
        return 30  # duración del servicio

    async def fetch(self, sql, *args):
        if sql == _SQL_OCUPADOS:
            self.pedidos.append(args)
            return []
        return []  # sin reglas semanales: ningún hueco


async def test_sin_excluir_turno_la_consulta_recibe_none_y_los_demas_llamadores_no_cambian():
    conn = ConnFalsa()
    await calcular_huecos(conn, uuid.uuid4(), uuid.uuid4(), RANGO)
    ((*_, excluido),) = conn.pedidos
    assert excluido is None


async def test_excluir_turno_viaja_a_la_consulta_de_ocupados():
    conn = ConnFalsa()
    turno = uuid.uuid4()
    await calcular_huecos(conn, uuid.uuid4(), uuid.uuid4(), RANGO, excluir_turno=turno)
    ((*_, excluido),) = conn.pedidos
    assert excluido == turno


def test_las_dos_ramas_de_turnos_de_la_consulta_excluyen_el_turno_propio():
    assert _SQL_OCUPADOS.count("$4::uuid is null or id <> $4::uuid") == 2
