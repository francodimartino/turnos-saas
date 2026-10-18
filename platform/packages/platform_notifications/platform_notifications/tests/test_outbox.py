"""Outbox de avisos: encolado en la transacción del llamador y cierre de la fila tras el envío (§6.4)."""

import asyncio
import json
import uuid

import httpx
import pytest

from platform_notifications import (
    CompletadorOutbox,
    CompletadorOutboxFuncion,
    EnvioPedido,
    TelegramAdapter,
    encolar_aviso,
    enviar_con_reintentos,
)
from platform_notifications.tests.conftest import cliente_con

CLIENTE = uuid.uuid4()
OK = httpx.Response(200, json={"ok": True, "result": {}})
FALLA = httpx.Response(500, json={"ok": False, "error_code": 500, "description": "boom"})
PERMANENTE = httpx.Response(403, json={"ok": False, "error_code": 403, "description": "bot bloqueado"})


class OutboxEnMemoria(CompletadorOutbox):
    def __init__(self) -> None:
        self.llamadas: list[dict] = []

    async def completar(self, outbox_id, status, last_error=None, retry_in_seconds=None):
        self.llamadas.append({"id": outbox_id, "status": status, "last_error": last_error})
        return True


class ConnGrabadora:
    def __init__(self, devuelve=None) -> None:
        self.sql: list[tuple] = []
        self.devuelve = devuelve

    async def fetchval(self, sql, *args):
        self.sql.append((sql, args))
        return self.devuelve


async def _sin_espera(_):
    return None


def _pedido(outbox_id):
    return EnvioPedido(end_customer_id=CLIENTE, destinatario="7", texto="hola", template="t", outbox_id=outbox_id)


async def test_encolar_aviso_llama_a_la_funcion_en_la_conexion_del_llamador_con_params_json():
    nuevo = uuid.uuid4()
    conn = ConnGrabadora(devuelve=nuevo)
    resultado = await encolar_aviso(
        conn, end_customer_id=CLIENTE, channel="telegram", template="turno_confirmado", params={"booking_id": "b1"}
    )
    assert resultado == nuevo
    (sql, args) = conn.sql[0]
    assert "enqueue_notification" in sql
    assert args[:3] == (CLIENTE, "telegram", "turno_confirmado")
    assert json.loads(args[3]) == {"booking_id": "b1"}
    assert args[4] == 300


async def test_encolar_aviso_devuelve_none_si_el_cliente_esta_anonimizado():
    assert await encolar_aviso(ConnGrabadora(None), end_customer_id=CLIENTE, channel="telegram", template="t") is None


async def test_envio_exitoso_cierra_la_fila_del_outbox_como_sent(token, registrador):
    outbox, oid = OutboxEnMemoria(), uuid.uuid4()
    async with cliente_con(lambda r: OK) as c:
        await enviar_con_reintentos(
            TelegramAdapter(token, c), registrador, _pedido(oid), esperar=_sin_espera, outbox=outbox
        )
    assert [(x["id"], x["status"]) for x in outbox.llamadas] == [(oid, "sent")]


async def test_envio_agotado_cierra_la_fila_como_failed_con_el_error(token, registrador):
    outbox, oid = OutboxEnMemoria(), uuid.uuid4()
    async with cliente_con(lambda r: FALLA) as c:
        await enviar_con_reintentos(
            TelegramAdapter(token, c), registrador, _pedido(oid), esperar=_sin_espera, outbox=outbox
        )
    assert len(outbox.llamadas) == 1
    assert outbox.llamadas[0]["status"] == "failed" and outbox.llamadas[0]["last_error"]


async def test_error_permanente_cierra_la_fila_como_failed_sin_reintentos(token, registrador):
    outbox, oid = OutboxEnMemoria(), uuid.uuid4()
    async with cliente_con(lambda r: PERMANENTE) as c:
        await enviar_con_reintentos(
            TelegramAdapter(token, c), registrador, _pedido(oid), esperar=_sin_espera, outbox=outbox
        )
    assert [x["status"] for x in outbox.llamadas] == ["failed"]


async def test_si_el_proceso_muere_antes_de_enviar_la_fila_queda_pending(token, registrador):
    """Un redeploy cancela la tarea en el medio del envío: nadie cierra la fila, el reescaneo la encuentra."""
    outbox, oid = OutboxEnMemoria(), uuid.uuid4()

    def handler(req):
        raise asyncio.CancelledError

    async with cliente_con(handler) as c:
        with pytest.raises(asyncio.CancelledError):
            await enviar_con_reintentos(
                TelegramAdapter(token, c), registrador, _pedido(oid), esperar=_sin_espera, outbox=outbox
            )
    assert outbox.llamadas == []


async def test_un_fallo_al_cerrar_la_fila_no_rompe_el_envio(token, registrador):
    class Roto(CompletadorOutbox):
        async def completar(self, *a, **k):
            raise RuntimeError("base caída")

    async with cliente_con(lambda r: OK) as c:
        nid = await enviar_con_reintentos(
            TelegramAdapter(token, c), registrador, _pedido(uuid.uuid4()), esperar=_sin_espera, outbox=Roto()
        )
    assert registrador.filas[nid]["status"] == "sent"


async def test_sin_outbox_id_no_se_toca_el_outbox(token, registrador):
    outbox = OutboxEnMemoria()
    async with cliente_con(lambda r: OK) as c:
        await enviar_con_reintentos(
            TelegramAdapter(token, c), registrador, _pedido(None), esperar=_sin_espera, outbox=outbox
        )
    assert outbox.llamadas == []


async def test_completador_funcion_usa_la_funcion_de_la_base_en_su_propia_transaccion():
    conn = ConnGrabadora(devuelve=True)

    class Tx:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *a):
            return False

    oid = uuid.uuid4()
    assert await CompletadorOutboxFuncion(lambda: Tx()).completar(oid, "failed", "x") is True
    (sql, args) = conn.sql[0]
    assert "complete_notification_outbox" in sql and args == (oid, "failed", "x", None)
