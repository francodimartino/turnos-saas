"""Webhook de Telegram: `POST /api/bot/telegram/{webhook_key}` (ARCHITECTURE.md §2.6, §3.7).

Orden de cada request (no se altera: cada paso depende del anterior):

1. `resolve_inbound_tenant('telegram', webhook_key)`: solo devuelve tenants `active`. Si no devuelve nada,
   200 y se descarta (para que Telegram no reintente). Es previo al contexto de tenant, por eso va por el pool
   sin transacción: la función es `SECURITY DEFINER` y no toca datos de tenant.
2. `tenant_tx`: abre la transacción y fija `app.current_tenant_id` con `set local`.
3. `get_integration_secret(integration_id)` y comparación en tiempo constante del header
   `X-Telegram-Bot-Api-Secret-Token`: si no coincide, 401 y rollback (no queda ninguna marca).
4. Deduplicación: `insert into inbound_messages ... on conflict do nothing`; si no insertó, ya se procesó: 200.
5. Se procesa en la MISMA transacción que la marca: si algo falla, el rollback borra la marca, se responde 500
   y la reentrega del canal se reprocesa.
6. Las respuestas se envían después del commit (como `BackgroundTasks`, con reintentos y registro).
"""

import hmac
import json
import logging
import uuid
from typing import Any

import sentry_sdk
from fastapi import APIRouter, BackgroundTasks, Request, Response
from fastapi.responses import JSONResponse
from observabilidad import contexto_log
from platform_notifications import (
    EnvioPedido,
    MensajeEntrante,
    RegistradorDirecto,
    RegistradorIntentos,
    TelegramAdapter,
    programar_envio,
)
from platform_secrets import AccesoSecretoDenegado, Secreto, crear_almacen
from platform_tenancy import tenant_tx

from bot_app.despachador import ContextoMensaje, Despachador, Respuesta
from bot_app.sesiones import borrar_sesion, guardar_sesion, leer_sesion

logger = logging.getLogger("bot.webhook")

CANAL = "telegram"
CABECERA_SECRET = "X-Telegram-Bot-Api-Secret-Token"

_RESOLVER = "select tenant_id, integration_id from public.resolve_inbound_tenant('telegram', $1)"
# `on conflict do nothing` no necesita SELECT sobre la tabla (bot_service solo tiene INSERT): el resultado
# del comando dice si insertó ("INSERT 0 1") o ya estaba ("INSERT 0 0").
_MARCAR = (
    "insert into public.inbound_messages (tenant_id, channel, external_message_id) "
    "values ($1, 'telegram', $2) on conflict do nothing"
)

router = APIRouter()


class _NoAutorizado(Exception):
    """El secret del header no coincide (o la integración no tiene uno): 401 y rollback."""


class _RegistradorNulo(RegistradorIntentos):
    """Envío sin fila en `notifications_log`: la respuesta aún no está ligada a un end_customer."""

    async def registrar(self, **_: Any) -> uuid.UUID:
        return uuid.uuid4()


def _credencial(valor: str | None) -> tuple[Secreto, Secreto] | None:
    """`(bot_token, webhook_secret)` del JSON de la integración, o `None` si no tiene la forma esperada."""
    if valor is None:
        return None
    try:
        datos = json.loads(valor)
    except ValueError:
        return None
    if not isinstance(datos, dict):
        return None
    token, secreto = datos.get("bot_token"), datos.get("webhook_secret")
    if not (isinstance(token, str) and token and isinstance(secreto, str) and secreto):
        return None
    return Secreto(token), Secreto(secreto)


def _coincide(recibido: str | None, esperado: Secreto) -> bool:
    # compare_digest sobre bytes: tiempo constante e indiferente a caracteres no ASCII del header.
    return hmac.compare_digest((recibido or "").encode("utf-8"), esperado.revelar().encode("utf-8"))


async def _procesar(
    conn: Any, tenant_id: uuid.UUID, mensaje: MensajeEntrante, despachador: Despachador
) -> list[Respuesta]:
    sesion = await leer_sesion(conn, tenant_id, mensaje.canal, mensaje.usuario_id)
    ctx = ContextoMensaje(conn, tenant_id, mensaje, dict(sesion or {}))
    await despachador.despachar(ctx)
    if ctx.descartar_sesion or (not ctx.sesion and sesion is not None):
        await borrar_sesion(conn, tenant_id, mensaje.canal, mensaje.usuario_id)
    elif ctx.sesion or sesion is not None:
        # También se guarda si no cambió: cada mensaje renueva la actividad (vence a la hora sin mensajes).
        await guardar_sesion(conn, tenant_id, mensaje.canal, mensaje.usuario_id, ctx.sesion)
    return ctx.respuestas


async def _leer_json(request: Request) -> Any:
    try:
        return json.loads(await request.body())
    except ValueError:
        return None


def _enviar_respuestas(
    tareas: BackgroundTasks, pool: Any, tenant_id: uuid.UUID, adapter: TelegramAdapter, respuestas: list[Respuesta]
) -> None:
    directo = RegistradorDirecto(lambda: tenant_tx(pool, tenant_id))
    for r in respuestas:
        registrador = directo if r.end_customer_id is not None else _RegistradorNulo()
        pedido = EnvioPedido(
            end_customer_id=r.end_customer_id or uuid.UUID(int=0),
            destinatario=r.destinatario,
            texto=r.texto,
            template=r.template,
            botones=r.botones,
        )
        programar_envio(tareas, adapter, registrador, pedido)


@router.post("/api/bot/telegram/{webhook_key}")
async def webhook_telegram(webhook_key: str, request: Request, tareas: BackgroundTasks) -> Response:
    estado = request.app.state
    pool, despachador = estado.pool, estado.despachador
    try:
        resuelto = await pool.fetchrow(_RESOLVER, webhook_key)
        if resuelto is None:
            logger.info("update_descartado", extra={"motivo": "tenant_no_activo_o_desconocido"})
            return Response(status_code=200)
        tenant_id, integration_id = resuelto["tenant_id"], resuelto["integration_id"]
        with contexto_log(tenant_id=str(tenant_id), actor_type="bot"):
            respuestas: list[Respuesta] = []
            adapter = None
            try:
                async with tenant_tx(pool, tenant_id) as conn:
                    try:
                        valor = await crear_almacen(conn).obtener(integration_id)
                    except AccesoSecretoDenegado:
                        raise _NoAutorizado from None
                    credencial = _credencial(None if valor is None else valor.revelar())
                    if credencial is None:
                        logger.error("credencial_de_integracion_invalida")
                        raise _NoAutorizado
                    token, esperado = credencial
                    if not _coincide(request.headers.get(CABECERA_SECRET), esperado):
                        raise _NoAutorizado
                    mensaje = TelegramAdapter.parsear_update(await _leer_json(request))
                    if mensaje is None or not mensaje.update_id:
                        logger.info("update_descartado", extra={"motivo": "no_soportado"})
                        return Response(status_code=200)
                    insertado = await conn.execute(_MARCAR, tenant_id, mensaje.update_id)
                    if insertado != "INSERT 0 1":
                        logger.info("update_duplicado", extra={"update_id": mensaje.update_id})
                        return Response(status_code=200)
                    respuestas = await _procesar(conn, tenant_id, mensaje, despachador)
                    adapter = TelegramAdapter(token, estado.cliente_http)
            except _NoAutorizado:
                logger.warning("secret_invalido")
                return Response(status_code=401)
            logger.info("update_procesado", extra={"update_id": mensaje.update_id, "respuestas": len(respuestas)})
            # Ya hay commit: recién ahora se responde al cliente final.
            if adapter is not None and respuestas:
                _enviar_respuestas(tareas, pool, tenant_id, adapter, respuestas)
            return Response(status_code=200)
    except Exception as error:  # noqa: BLE001 - 500 para que el canal reintente; la marca ya se revirtió
        logger.exception("error_procesando_update")
        sentry_sdk.capture_exception(error)
        return JSONResponse({"detail": "error interno"}, status_code=500)
