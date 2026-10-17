"""Registradores SQL con una conexión falsa: qué sentencia usa cada uno y con qué argumentos."""

import uuid
from contextlib import asynccontextmanager

from platform_notifications import RegistradorFuncion, RegistradorIntentos

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


def test_no_existe_un_registrador_con_sql_directo():
    import platform_notifications

    assert not hasattr(platform_notifications, "RegistradorDirecto")
