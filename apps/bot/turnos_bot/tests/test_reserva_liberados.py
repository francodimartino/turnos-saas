"""Envío posterior al commit del aviso al cliente liberado: cierra la fila del outbox como sent o failed."""

import contextlib
import uuid
from datetime import datetime, timezone

import httpx
from platform_notifications import ErrorEnvio
from turnos_dominio import AvisoLiberado, Liberado
from turnos_negocio import mensaje_vencimiento

from turnos_bot.despachador import Recursos
from turnos_bot.reserva import liberados

UTC = timezone.utc  # noqa: UP017
TENANT = uuid.uuid4()
INICIO = datetime(2026, 10, 21, 15, 30, tzinfo=UTC)


def aviso() -> AvisoLiberado:
    lib = Liberado(TENANT, uuid.uuid4(), "telegram", "222", True, uuid.uuid4(), INICIO, "Corte")
    return AvisoLiberado(lib, uuid.uuid4())


class Escenario:
    def __init__(self, monkeypatch, error=None, credencial=True) -> None:
        self.enviados: list[tuple] = []
        self.cierres: list[tuple] = []
        self.error = error

        @contextlib.asynccontextmanager
        async def tenant_tx(pool, tenant):
            yield object()

        class Adapter:
            canal = "telegram"

            async def send_message(inner, destinatario, texto):  # noqa: N805
                self.enviados.append((destinatario, texto))
                if self.error:
                    raise self.error

        async def adapter(almacen, cliente, **_):
            if not credencial:
                raise liberados.ErrorCredencial("sin integración")
            return Adapter()

        class Completador:
            def __init__(inner, abrir) -> None:  # noqa: N805
                pass

            async def completar(inner, outbox_id, status, last_error=None, retry_in_seconds=None):  # noqa: N805
                self.cierres.append((outbox_id, status))
                return True

        class Registrador:
            def __init__(inner, abrir) -> None:  # noqa: N805
                pass

            async def registrar(inner, **datos):  # noqa: N805
                return uuid.uuid4()

        monkeypatch.setattr(liberados, "tenant_tx", tenant_tx)
        monkeypatch.setattr(liberados, "adapter_telegram_del_tenant", adapter)
        monkeypatch.setattr(liberados, "crear_almacen", lambda conn: None)
        monkeypatch.setattr(liberados, "CompletadorOutboxFuncion", Completador)
        monkeypatch.setattr(liberados, "RegistradorFuncion", Registrador)

        async def sin_espera(_):
            return None

        original = liberados.enviar_con_reintentos

        async def enviar(adapter, registrador, pedido, **kw):
            return await original(adapter, registrador, pedido, backoff=(0, 0, 0), esperar=sin_espera, **kw)

        monkeypatch.setattr(liberados, "enviar_con_reintentos", enviar)

    async def correr(self, avisos) -> None:
        recursos = Recursos(None, httpx.AsyncClient(), None)
        try:
            await liberados.enviar_avisos_liberados(avisos)(recursos)
        finally:
            await recursos.cliente_http.aclose()


async def test_el_envio_exitoso_manda_el_texto_compartido_y_cierra_la_fila_sent(monkeypatch):
    esc = Escenario(monkeypatch)
    a = aviso()
    await esc.correr([a])
    assert esc.enviados == [("222", mensaje_vencimiento(INICIO, "Corte"))]
    assert esc.cierres == [(a.outbox_id, "sent")]


async def test_un_error_permanente_cierra_la_fila_failed(monkeypatch):
    esc = Escenario(monkeypatch, error=ErrorEnvio("forbidden", "bot bloqueado", estado_http=403))
    a = aviso()
    await esc.correr([a])
    assert esc.cierres == [(a.outbox_id, "failed")]


async def test_sin_credencial_cierra_la_fila_failed_y_no_corta_los_demas(monkeypatch):
    esc = Escenario(monkeypatch, credencial=False)
    uno, otro = aviso(), aviso()
    await esc.correr([uno, otro])
    assert esc.enviados == []
    assert esc.cierres == [(uno.outbox_id, "failed"), (otro.outbox_id, "failed")]
