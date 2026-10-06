"""Repositorio de reservas con una conexión simulada: vencimiento, savepoint y traducción del exclusion constraint."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from bot_app.reserva import repositorio

UTC = timezone.utc  # noqa: UP017
AHORA = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def test_el_vencimiento_es_el_minimo_entre_el_timeout_y_el_inicio_del_turno():
    lejos = AHORA + timedelta(days=3)
    cerca = AHORA + timedelta(hours=5)
    assert repositorio.calcular_vencimiento(AHORA, lejos, 24) == AHORA + timedelta(hours=24)
    assert repositorio.calcular_vencimiento(AHORA, cerca, 24) == cerca
    assert repositorio.calcular_vencimiento(AHORA, AHORA + timedelta(hours=24), 24) == AHORA + timedelta(hours=24)


class ErrorPg(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__("error de base")
        self.sqlstate = sqlstate


class ConnFalsa:
    def __init__(
        self, error: Exception | None = None, fila: dict | None = None, error_lock: Exception | None = None
    ) -> None:
        self.error, self.fila, self.error_lock = error, fila, error_lock
        self.sentencias: list[str] = []

    async def execute(self, sql, *args):
        self.sentencias.append("lock" if "pg_advisory_xact_lock" in sql else sql)
        if self.error_lock and "pg_advisory_xact_lock" in sql:
            raise self.error_lock

    async def fetchrow(self, sql, *args):
        self.sentencias.append("insert")
        if self.error:
            raise self.error
        return self.fila

    async def fetchval(self, sql, *args):
        return self.fila


DATOS = {
    "tenant_id": uuid.uuid4(),
    "end_customer_id": uuid.uuid4(),
    "servicio_id": uuid.uuid4(),
    "profesional_id": uuid.uuid4(),
    "inicio": AHORA + timedelta(days=1),
    "fin": AHORA + timedelta(days=1, minutes=30),
    "pending_expires_at": None,
}


async def test_el_insert_exitoso_libera_el_savepoint():
    conn = ConnFalsa(fila={"id": uuid.uuid4(), "status": "confirmed", "pending_expires_at": None})
    creado = await repositorio.crear_booking(conn, **DATOS)
    assert creado.status == "confirmed" and creado.pending_expires_at is None
    assert [s.split()[0] for s in conn.sentencias] == ["savepoint", "lock", "insert", "release"]


async def test_la_violacion_del_exclusion_constraint_es_un_choque_no_un_error():
    conn = ConnFalsa(error=ErrorPg("23P01"))
    assert await repositorio.crear_booking(conn, **DATOS) is None
    # la transacción del webhook sigue sana: se volvió al savepoint
    assert conn.sentencias[-1].startswith("rollback to savepoint")


async def test_cualquier_otro_error_se_propaga():
    conn = ConnFalsa(error=ErrorPg("23503"))
    with pytest.raises(ErrorPg):
        await repositorio.crear_booking(conn, **DATOS)
    assert conn.sentencias[-1].startswith("rollback to savepoint")


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [(None, 24), ("12", 12), ("168", 168), ("0", 24), ("169", 24), ("abc", 24), ("2.5", 24), ("-3", 24)],
)
async def test_horas_de_aprobacion_con_default_de_24_ante_un_valor_invalido(valor, esperado):
    conn = ConnFalsa(fila=valor)
    assert await repositorio.horas_aprobacion(conn, uuid.uuid4()) == esperado


async def test_el_lock_del_profesional_se_toma_dentro_del_savepoint_y_antes_del_insert():
    conn = ConnFalsa(fila={"id": uuid.uuid4(), "status": "confirmed", "pending_expires_at": None})
    await repositorio.crear_booking(conn, **DATOS)
    assert conn.sentencias.index("lock") == 1  # savepoint, lock, insert: el lock se libera con el rollback


@pytest.mark.parametrize("sqlstate", ["40P01", "40001"])
async def test_un_deadlock_o_serializacion_residual_es_un_choque_no_un_error(sqlstate):
    conn = ConnFalsa(error=ErrorPg(sqlstate))
    assert await repositorio.crear_booking(conn, **DATOS) is None
    assert conn.sentencias[-1].startswith("rollback to savepoint")


async def test_un_deadlock_al_tomar_el_lock_tambien_es_un_choque():
    conn = ConnFalsa(error_lock=ErrorPg("40P01"))
    assert await repositorio.crear_booking(conn, **DATOS) is None
    assert conn.sentencias[-1].startswith("rollback to savepoint")
    assert "insert" not in conn.sentencias
