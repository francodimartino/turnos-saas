"""La clave del lock por profesional es una sola, compartida por dashboard-backend y el bot."""

import uuid

from dashboard_backend import turnos
from platform_tenancy.testing import TransaccionPorExecute

from turnos_queries import bloqueo


class ConnFalsa(TransaccionPorExecute):
    def __init__(self) -> None:
        self.ejecuciones: list[tuple] = []

    async def execute(self, sql, *args):
        self.ejecuciones.append((sql, *args))


def test_el_sql_del_lock_es_por_tenant_y_profesional_y_de_transaccion():
    assert "pg_advisory_xact_lock" in bloqueo.SQL_BLOQUEAR_PROFESIONAL
    assert "$1" in bloqueo.SQL_BLOQUEAR_PROFESIONAL and "$2" in bloqueo.SQL_BLOQUEAR_PROFESIONAL


def test_dashboard_usa_exactamente_la_misma_sentencia():
    assert turnos.SQL_BLOQUEAR_PROFESIONAL is bloqueo.SQL_BLOQUEAR_PROFESIONAL


async def test_bloquear_profesional_pasa_los_ids_como_texto():
    conn, tenant, prof = ConnFalsa(), uuid.uuid4(), uuid.uuid4()
    await bloqueo.bloquear_profesional(conn, tenant_id=tenant, profesional_id=prof)
    assert conn.ejecuciones == [(bloqueo.SQL_BLOQUEAR_PROFESIONAL, str(tenant), str(prof))]
