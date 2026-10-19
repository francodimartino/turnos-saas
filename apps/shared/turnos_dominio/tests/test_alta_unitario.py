"""Alta y movimiento de turnos con una conexión simulada: orden, límites, choque y filtro por tenant."""

import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from turnos_queries.tests.test_estatico import tablas_sin_filtro

from turnos_dominio import alta, limites

UTC = timezone.utc  # noqa: UP017
AHORA = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
LIMITES = limites.LimitesReserva(max_activos_por_cliente=2, max_por_hora=3, anticipacion_minutos=30)


class ErrorPg(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__("error de base")
        self.sqlstate = sqlstate


class ConnFalsa:
    def __init__(self, error=None, fila=None, activos=0, ultima_hora=0, liberados=(), pack=True):
        self.error, self.fila, self.conteo, self.pack = error, fila, (activos, ultima_hora), pack
        self.liberados = list(liberados)
        self.sentencias: list[str] = []
        self.sql: list[str] = []

    async def execute(self, sql, *args):
        self.sql.append(sql)
        self.sentencias.append("lock" if "pg_advisory_xact_lock" in sql else sql)

    async def fetch(self, sql, *args):
        self.sql.append(sql)
        self.sentencias.append("liberar")
        return self.liberados

    async def fetchrow(self, sql, *args):
        self.sql.append(sql)
        if "booking_packs" in sql:
            self.sentencias.append("pack")
            return {"id": uuid.uuid4()} if self.pack else None
        if "count(*)" in sql:
            self.sentencias.append("conteo")
            return self.conteo
        self.sentencias.append("escritura")
        if self.error:
            raise self.error
        return self.fila


FILA = {"id": uuid.uuid4(), "status": "confirmed", "pending_expires_at": None}
DATOS = {
    "tenant_id": uuid.uuid4(),
    "end_customer_id": uuid.uuid4(),
    "servicio_id": uuid.uuid4(),
    "profesional_id": uuid.uuid4(),
    "inicio": AHORA + timedelta(days=1),
    "fin": AHORA + timedelta(days=1, minutes=30),
}
MOVER = {
    "tenant_id": DATOS["tenant_id"],
    "booking_id": uuid.uuid4(),
    "profesional_id": DATOS["profesional_id"],
    "inicio": DATOS["inicio"],
    "fin": DATOS["fin"],
}


def _palabras(conn):
    return [s.split()[0] for s in conn.sentencias]


async def test_sin_reglas_de_canal_solo_toma_el_lock_del_profesional_e_inserta():
    conn = ConnFalsa(fila=FILA)
    creado = await alta.crear_turno(conn, **DATOS, estado="confirmed")
    assert creado.status == "confirmed"
    assert _palabras(conn) == ["savepoint", "lock", "escritura", "release"]


async def test_orden_con_las_reglas_del_bot_lock_cliente_lock_profesional_limites_liberacion_insercion():
    conn = ConnFalsa(fila=FILA, activos=1, ultima_hora=2)
    await alta.crear_turno(conn, **DATOS, estado="confirmed", limites=LIMITES, liberar_vencidos=True, ahora=AHORA)
    assert _palabras(conn) == ["savepoint", "lock", "lock", "conteo", "liberar", "escritura", "release"]
    assert "turnos_cliente" in conn.sql[1] and "turnos_profesional" in conn.sql[2]


async def test_con_pack_consume_una_sesion_despues_del_lock_y_antes_de_insertar():
    conn = ConnFalsa(fila=FILA)
    await alta.crear_turno(conn, **DATOS, estado="confirmed", booking_pack_id=uuid.uuid4())
    assert _palabras(conn) == ["savepoint", "lock", "pack", "escritura", "release"]


async def test_un_pack_no_disponible_levanta_sin_insertar_y_vuelve_al_savepoint():
    conn = ConnFalsa(fila=FILA, pack=False)
    with pytest.raises(alta.PackNoDisponible):
        await alta.crear_turno(conn, **DATOS, estado="confirmed", booking_pack_id=uuid.uuid4())
    assert "escritura" not in conn.sentencias
    assert conn.sentencias[-1].startswith("rollback to savepoint")


async def test_sin_pack_no_se_toca_booking_packs():
    conn = ConnFalsa(fila=FILA)
    await alta.crear_turno(conn, **DATOS, estado="confirmed")
    assert "pack" not in conn.sentencias


@pytest.mark.parametrize("sqlstate", ["40P01", "40001"])
async def test_el_canal_puede_pedir_que_la_concurrencia_se_propague_para_reintentar_su_transaccion(sqlstate):
    conn = ConnFalsa(error=ErrorPg(sqlstate))
    with pytest.raises(ErrorPg):
        await alta.crear_turno(conn, **DATOS, estado="confirmed", concurrencia_como_choque=False)
    assert conn.sentencias[-1].startswith("rollback to savepoint")
    conn = ConnFalsa(error=ErrorPg("23P01"))  # el choque real sigue siendo resultado
    assert await alta.crear_turno(conn, **DATOS, estado="confirmed", concurrencia_como_choque=False) is None
    with pytest.raises(ErrorPg):
        await alta.mover_turno(ConnFalsa(error=ErrorPg(sqlstate)), **MOVER, concurrencia_como_choque=False)
    movido = await alta.mover_turno(ConnFalsa(error=ErrorPg("23P01")), **MOVER, concurrencia_como_choque=False)
    assert movido == "choque"


@pytest.mark.parametrize("sqlstate", ["23P01", "40P01", "40001"])
async def test_un_choque_devuelve_none_y_vuelve_al_savepoint(sqlstate):
    conn = ConnFalsa(error=ErrorPg(sqlstate))
    assert await alta.crear_turno(conn, **DATOS, estado="confirmed") is None
    assert conn.sentencias[-1].startswith("rollback to savepoint")


async def test_otro_error_se_propaga_tras_volver_al_savepoint():
    conn = ConnFalsa(error=ErrorPg("23503"))
    with pytest.raises(ErrorPg):
        await alta.crear_turno(conn, **DATOS, estado="confirmed")
    assert conn.sentencias[-1].startswith("rollback to savepoint")


@pytest.mark.parametrize(
    ("kwargs", "motivo"),
    [
        ({"activos": 2}, limites.MOTIVO_TOPE_ACTIVOS),
        ({"ultima_hora": 3}, limites.MOTIVO_LIMITE_POR_HORA),
    ],
)
async def test_un_limite_excedido_rechaza_sin_insertar(kwargs, motivo):
    conn = ConnFalsa(fila=FILA, **kwargs)
    with pytest.raises(limites.ReservaRechazada) as error:
        await alta.crear_turno(conn, **DATOS, estado="confirmed", limites=LIMITES, ahora=AHORA)
    assert error.value.motivo == motivo
    assert "escritura" not in conn.sentencias and "liberar" not in conn.sentencias
    assert conn.sentencias[-1].startswith("rollback to savepoint")


async def test_la_anticipacion_minima_rechaza_antes_de_contar():
    conn = ConnFalsa(fila=FILA)
    datos = {**DATOS, "inicio": AHORA + timedelta(minutes=29)}
    with pytest.raises(limites.ReservaRechazada) as error:
        await alta.crear_turno(conn, **datos, estado="confirmed", limites=LIMITES, ahora=AHORA)
    assert error.value.motivo == limites.MOTIVO_ANTICIPACION
    assert "conteo" not in conn.sentencias


async def test_los_liberados_vuelven_en_el_resultado_y_antes_de_insertar():
    liberado = {
        "tenant_id": DATOS["tenant_id"],
        "booking_id": uuid.uuid4(),
        "channel": "telegram",
        "channel_user_id": "99",
        "notify": True,
        "end_customer_id": uuid.uuid4(),
        "starts_at": AHORA,
        "service_name": "Corte",
    }
    conn = ConnFalsa(fila=FILA, liberados=[liberado])
    creado = await alta.crear_turno(conn, **DATOS, estado="confirmed", liberar_vencidos=True)
    assert [x.booking_id for x in creado.liberados] == [liberado["booking_id"]]
    assert conn.sentencias.index("liberar") < conn.sentencias.index("escritura")


async def test_sin_liberar_vencidos_no_se_llama_a_la_funcion():
    conn = ConnFalsa(fila=FILA)
    await alta.crear_turno(conn, **DATOS, estado="confirmed")
    assert "liberar" not in conn.sentencias


def test_el_estado_pending_exige_vencimiento_y_el_confirmed_no_lo_admite():
    alta._validar_estado("confirmed", None)
    alta._validar_estado("pending", AHORA)
    with pytest.raises(ValueError):
        alta._validar_estado("pending", None)
    with pytest.raises(ValueError):
        alta._validar_estado("confirmed", AHORA)
    with pytest.raises(ValueError):
        alta._validar_estado("cancelled", None)


async def test_mover_toma_el_lock_y_hace_un_solo_update():
    conn = ConnFalsa(fila={"id": uuid.uuid4()})
    assert await alta.mover_turno(conn, **MOVER) == "movido"
    assert _palabras(conn) == ["savepoint", "lock", "escritura", "release"]
    assert sum("update public.bookings" in s for s in conn.sql) == 1


async def test_mover_a_un_horario_ocupado_es_choque_y_a_un_turno_inexistente_no_existe():
    assert await alta.mover_turno(ConnFalsa(error=ErrorPg("23P01")), **MOVER) == "choque"
    assert await alta.mover_turno(ConnFalsa(error=ErrorPg("40001")), **MOVER) == "choque"
    assert await alta.mover_turno(ConnFalsa(fila=None), **MOVER) == "no_existe"
    with pytest.raises(ErrorPg):
        await alta.mover_turno(ConnFalsa(error=ErrorPg("23503")), **MOVER)


SENTENCIAS = {n: v for n, v in vars(alta).items() if n.startswith("SQL_") and isinstance(v, str)}


def test_el_modulo_declara_todas_sus_sentencias():
    assert set(SENTENCIAS) == {
        "SQL_BLOQUEAR_CLIENTE",
        "SQL_CONSUMIR_PACK",
        "SQL_CONTAR_RESERVAS",
        "SQL_INSERTAR_TURNO",
        "SQL_MOVER_TURNO",
        "SQL_LIBERAR_VENCIDOS",
    }


@pytest.mark.parametrize("nombre", sorted(SENTENCIAS))
def test_toda_sentencia_filtra_por_tenant_id_explicito(nombre):
    sql = SENTENCIAS[nombre]
    if nombre == "SQL_LIBERAR_VENCIDOS":
        # Función de la base: resuelve el tenant desde el profesional y corre bajo RLS; no recibe tenant_id.
        assert re.search(r"release_expired_pending_overlaps\(\$1,", sql)
    elif sql.lstrip().lower().startswith("insert"):
        assert "(tenant_id," in sql and "values ($1," in sql
    elif "pg_advisory_xact_lock" in sql:
        assert "$1::text" in sql  # la clave del lock lleva el tenant como primer componente
    else:
        assert tablas_sin_filtro(sql) == [], f"{nombre}: falta `tenant_id = $1`"
        assert re.search(r"tenant_id\s*=\s*\$1", sql)
