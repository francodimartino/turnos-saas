"""Envío con reintentos contra `notifications_log` real, con los roles que lo usan (bot_service y scheduler_job)."""

import uuid
from contextlib import asynccontextmanager

import httpx
import pytest

from platform_notifications import (
    EnvioPedido,
    RegistradorDirecto,
    RegistradorFuncion,
    TelegramAdapter,
    enviar_con_reintentos,
)
from platform_notifications.tests.conftest import cliente_con

pytestmark = pytest.mark.integracion

asyncpg = pytest.importorskip("asyncpg")

SENSIBLE = "Hola Ana, tu turno de depilación con DNI 30111222"


@pytest.fixture
async def escenario(db_superuser):
    tenant, cliente = uuid.uuid4(), uuid.uuid4()
    await db_superuser.execute(
        "insert into public.tenants (id, name, slug) values ($1, 'N', $2)", tenant, f"it-notif-{uuid.uuid4().hex[:8]}"
    )
    await db_superuser.execute(
        "insert into public.end_customers (id, tenant_id, channel, channel_user_id) "
        "values ($1, $2, 'telegram', 'u-it')",
        cliente,
        tenant,
    )
    try:
        yield tenant, cliente
    finally:
        await db_superuser.execute("delete from public.notifications_log where tenant_id = $1", tenant)
        await db_superuser.execute("delete from public.end_customers where tenant_id = $1", tenant)
        await db_superuser.execute("delete from public.tenants where id = $1", tenant)


def _abrir(conn, tenant):
    @asynccontextmanager
    async def abrir():
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute("select set_config('app.current_tenant_id', $1, true)", str(tenant))
            yield conn
        except BaseException:
            await tx.rollback()
            raise
        else:
            await tx.commit()

    return abrir


def _falla(token):
    def handler(req):
        return httpx.Response(400, json={"ok": False, "error_code": 400, "description": f"mal {SENSIBLE} {token}"})

    return handler


async def _sin_espera(_):
    return None


@pytest.mark.parametrize(
    ("rol", "registrador_cls"), [("bot_service", RegistradorDirecto), ("scheduler_job", RegistradorFuncion)]
)
async def test_falla_tras_3_reintentos_sin_guardar_texto_ni_token(
    conectar_como, db_superuser, escenario, token, rol, registrador_cls
):
    tenant, cliente = escenario
    conn = await conectar_como(rol)
    registrador = registrador_cls(_abrir(conn, tenant))
    pedido = EnvioPedido(end_customer_id=cliente, destinatario="1", texto=SENSIBLE, template="turno_vencido")
    async with cliente_con(_falla(token)) as c:
        nid = await enviar_con_reintentos(TelegramAdapter(token, c), registrador, pedido, esperar=_sin_espera)
    fila = await db_superuser.fetchrow(
        "select status, retry_count, last_error, channel, template from public.notifications_log where id = $1", nid
    )
    assert (fila["status"], fila["retry_count"], fila["channel"], fila["template"]) == (
        "failed", 3, "telegram", "turno_vencido",
    )  # fmt: skip
    assert fila["last_error"].startswith("400:")
    assert SENSIBLE not in fila["last_error"] and token not in fila["last_error"]


async def test_exito_queda_sent_con_sent_at(conectar_como, db_superuser, escenario, token):
    tenant, cliente = escenario
    conn = await conectar_como("bot_service")
    registrador = RegistradorDirecto(_abrir(conn, tenant))
    pedido = EnvioPedido(end_customer_id=cliente, destinatario="1", texto="hola", template="t")
    async with cliente_con(lambda req: httpx.Response(200, json={"ok": True, "result": {}})) as c:
        nid = await enviar_con_reintentos(TelegramAdapter(token, c), registrador, pedido, esperar=_sin_espera)
    fila = await db_superuser.fetchrow("select status, sent_at from public.notifications_log where id = $1", nid)
    assert fila["status"] == "sent" and fila["sent_at"] is not None
