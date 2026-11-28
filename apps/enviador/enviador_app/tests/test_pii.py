"""Ni los logs ni Sentry reciben el email, el código, el token ni los datos del cliente (turnos_observabilidad)."""

import asyncio
import io
import json
import uuid
from unittest.mock import patch

import sentry_sdk
from platform_notifications import BACKOFF_POR_DEFECTO
from sentry_sdk.transport import Transport
from turnos_observabilidad import configurar_logging, iniciar_sentry

from enviador_app.bucle import Enviador
from enviador_app.procesador import ProcesadorOutbox
from enviador_app.reporte import reportar_a_sentry
from enviador_app.tests.soporte import (
    CHAT,
    EMAIL_CLIENTE,
    EMAIL_NEGOCIO,
    TOKEN,
    CorreoSimulado,
    Mundo,
    Telegram,
    destino,
    email,
    fila,
    sin_espera,
    telegram,
)

CODIGO = "482913"
NOMBRE = "Ana Perez Gonzalez"
TELEFONO = "+5491155550123"
TENANT = uuid.uuid4()
SECRETOS = (EMAIL_CLIENTE, EMAIL_NEGOCIO, CHAT, TOKEN, CODIGO, NOMBRE, TELEFONO, "Corte de pelo")


class CorreoRoto(CorreoSimulado):
    """Una librería que arma su excepción con todo lo sensible a mano: nada de eso puede llegar al log ni a Sentry."""

    async def enviar(self, destino_, asunto, texto, html=None):
        raise RuntimeError(f"fallo con {destino_} code={CODIGO} token={TOKEN} nombre={NOMBRE} tel={TELEFONO} {texto}")


def _telegram_que_repite_los_datos():
    """Bot API simulada cuya descripción de error repite el chat y el token del pedido."""

    class TelegramHostil(Telegram):
        def _responder(self, req):
            import httpx

            cuerpo = json.loads(req.content)
            self.pedidos.append((str(req.url), cuerpo))
            descripcion = f"chat {cuerpo['chat_id']} no existe, token {TOKEN}, mensaje {cuerpo['text']}"
            return httpx.Response(400, json={"ok": False, "error_code": 400, "description": descripcion})

    return TelegramHostil()


async def test_ni_los_logs_ni_sentry_reciben_datos_personales_ni_credenciales():
    eventos: list[dict] = []

    class Captura(Transport):
        def capture_envelope(self, envelope):
            if envelope.get_event() is not None:
                eventos.append(envelope.get_event())

    buffer = io.StringIO()
    configurar_logging(stream=buffer)
    with patch("turnos_observabilidad.sentry.sentry_sdk.init", wraps=sentry_sdk.init) as init:
        iniciar_sentry("https://" + "0123456789abcdef" * 2 + "@o1.ingest.sentry.io/1", "turnos-enviador")
    kwargs = {**init.call_args.kwargs, "transport": Captura()}
    sentry_sdk.init(**kwargs)
    try:
        mundo = Mundo()
        # 1) cliente con Telegram (el bot responde con un error que repite los datos) y email roto
        # 2) cliente solo con email y un 422
        # 3) aviso al negocio con un error inesperado
        f1, f2, f3 = fila(TENANT), fila(TENANT), fila(TENANT, "aviso_negocio_reserva", negocio=True)
        mundo.poner(f1, [telegram(), email()])
        mundo.poner(f2, [email()])
        mundo.poner(f3, [destino("email", EMAIL_NEGOCIO, clave="tu:1:email", destino_fila="negocio")])
        mundo.con_bot(TENANT)
        mundo.cola = [f1, f2, f3]
        correo = CorreoRoto()
        proc = ProcesadorOutbox(
            mundo,
            cliente_http=_telegram_que_repite_los_datos().cliente,
            cliente_email=correo,
            base_url_telegram="https://api.telegram.org",
            backoff=BACKOFF_POR_DEFECTO,
            esperar=sin_espera,
            max_intentos=2,
            reintento_seg=300,
            reportar=reportar_a_sentry,
        )
        env = Enviador(
            mundo,
            proc,
            lote=5,
            concurrencia=3,
            arriendo_seg=300,
            max_intentos=2,
            plazo_fila_seg=5.0,
            plazo_apagado_seg=1.0,
            intervalo_seg=1.0,
            espera_maxima_seg=2.0,
            reportar=reportar_a_sentry,
        )
        # `f1` y `f2` llegan en su último intento: fallan de verdad y se reportan
        f1["attempts"] = f2["attempts"] = f3["attempts"] = 2
        await env.ciclo(asyncio.Event())
        # un fallo del reclamo también se reporta
        mundo.falla_reclamo = [RuntimeError(f"base caída para {EMAIL_CLIENTE}")]
        env._en_fallo = False
        try:
            await env.ciclo(asyncio.Event())
        except RuntimeError as error:
            env._fallo_del_ciclo(error)
        sentry_sdk.flush()
    finally:
        sentry_sdk.get_client().close()
        sentry_sdk.init()  # deja el SDK desactivado
    assert env.contadores.fallos == 3
    assert eventos, "el servicio tiene que haber reportado los fallos"
    salida = buffer.getvalue() + json.dumps(eventos, default=str)
    for secreto in SECRETOS:
        assert secreto not in salida, secreto
    # lo que sí se reporta: el tenant y un motivo corto
    etiquetas = [e["tags"] for e in eventos if e.get("tags")]
    assert {"motivo": "telegram:http_400, email:interno", "tenant_id": str(TENANT)} in etiquetas
    assert any(t.get("motivo") == "RuntimeError" for t in etiquetas)
