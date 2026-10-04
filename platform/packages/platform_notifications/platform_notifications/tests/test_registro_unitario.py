"""Registradores SQL con una conexión falsa: qué sentencia usa cada uno y con qué argumentos."""

import uuid
from contextlib import asynccontextmanager

import pytest

from platform_notifications import RegistradorDirecto, RegistradorFuncion, RegistradorIntentos

CLIENTE = uuid.uuid4()


class ConexionFalsa:
    def __init__(self, resultado):
        self.resultado = resultado
        self.llamadas = []

    async def fetchval(self, sql, *args):
        self.llamadas.append((sql, args))
        return self.resultado


def abrir_con(conn):
    abiertas = []

    @asynccontextmanager
    async def abrir():
        abiertas.append(1)
        yield conn

    return abrir, abiertas


def test_los_registradores_cumplen_la_interfaz():
    assert issubclass(RegistradorDirecto, RegistradorIntentos)
    assert issubclass(RegistradorFuncion, RegistradorIntentos)


async def test_funcion_llama_a_record_notification_attempt():
    nid = uuid.uuid4()
    conn = ConexionFalsa(nid)
    abrir, abiertas = abrir_con(conn)
    devuelto = await RegistradorFuncion(abrir).registrar(
        end_customer_id=CLIENTE, channel="telegram", template="turno_vencido", status="failed",
        retry_count=3, last_error="500: boom", notification_id=nid,
    )  # fmt: skip
    ((sql, args),) = conn.llamadas
    assert "public.record_notification_attempt" in sql
    assert args == (CLIENTE, "telegram", "turno_vencido", "failed", 3, "500: boom", nid)
    assert devuelto == nid and abiertas == [1]


async def test_directo_inserta_sin_notification_id():
    nid = uuid.uuid4()
    conn = ConexionFalsa(nid)
    abrir, _ = abrir_con(conn)
    devuelto = await RegistradorDirecto(abrir).registrar(
        end_customer_id=CLIENTE, channel="telegram", template="t", status="pending"
    )
    ((sql, args),) = conn.llamadas
    assert sql.startswith("insert into public.notifications_log")
    assert args == (CLIENTE, "telegram", "t", "pending", 0, None)
    assert devuelto == nid


async def test_directo_actualiza_solo_pendientes():
    nid = uuid.uuid4()
    conn = ConexionFalsa(nid)
    abrir, _ = abrir_con(conn)
    await RegistradorDirecto(abrir).registrar(
        end_customer_id=CLIENTE, channel="telegram", template="t", status="sent", retry_count=1, notification_id=nid
    )
    ((sql, args),) = conn.llamadas
    assert sql.startswith("update public.notifications_log") and "status = 'pending'" in sql
    assert args == (nid, "sent", 1, None, CLIENTE)


async def test_directo_falla_si_la_fila_no_se_actualizo():
    abrir, _ = abrir_con(ConexionFalsa(None))
    with pytest.raises(LookupError):
        await RegistradorDirecto(abrir).registrar(
            end_customer_id=CLIENTE, channel="telegram", template="t", status="sent", notification_id=uuid.uuid4()
        )
