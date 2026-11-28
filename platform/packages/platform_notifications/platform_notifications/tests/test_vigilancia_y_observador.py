"""`CanalVigilado`, `SinRegistro` y `ObservadorDeIntentos`: lo que comparten el job de vencimiento y el enviador."""

import uuid

import pytest

from platform_notifications import (
    CanalVigilado,
    ErrorEnvio,
    ObservadorDeIntentos,
    RegistradorIntentos,
    SinRegistro,
    TelegramAdapter,
)


class AdapterQueFalla(TelegramAdapter):
    def __init__(self, error=None):
        super().__init__("123456:token-de-prueba")
        self.error = error
        self.enviados = []

    async def send_message(self, destinatario, texto):
        if self.error is not None:
            raise self.error
        self.enviados.append((destinatario, texto))


class RegistradorQueGuarda(RegistradorIntentos):
    def __init__(self, falla=None):
        self.llamadas = []
        self.falla = falla

    async def registrar(self, **datos):
        if self.falla is not None:
            raise self.falla
        self.llamadas.append(datos)
        return datos.get("notification_id") or uuid.uuid4()


async def test_el_canal_vigilado_envia_y_recuerda_el_ultimo_error():
    real = AdapterQueFalla(ErrorEnvio("403", "bloqueado", estado_http=403))
    vigilado = CanalVigilado(real)
    assert vigilado.canal == "telegram" and vigilado.ultimo_error is None
    with pytest.raises(ErrorEnvio):
        await vigilado.send_message("chat", "texto")
    assert vigilado.ultimo_error is not None and not vigilado.ultimo_error.reintentable
    real.error = None
    await vigilado.send_message("chat", "hola")
    assert real.enviados == [("chat", "hola")]


async def test_sin_registro_devuelve_un_id_y_no_escribe_nada():
    nuevo = await SinRegistro().registrar(end_customer_id=None, channel="email", template="x", status="pending")
    existente = uuid.uuid4()
    assert isinstance(nuevo, uuid.UUID)
    assert (
        await SinRegistro().registrar(
            end_customer_id=None, channel="email", template="x", status="sent", notification_id=existente
        )
        == existente
    )


async def _intento(observador, **cambios):
    datos = {"end_customer_id": uuid.uuid4(), "channel": "email", "template": "t", "status": "pending", **cambios}
    return await observador.registrar(**datos)


async def test_el_observador_recuerda_estado_y_error_y_delega():
    real = RegistradorQueGuarda()
    observador = ObservadorDeIntentos(real)
    await _intento(observador, status="failed", last_error="boom")
    assert (observador.estado, observador.ultimo_error) == ("failed", "boom") and len(real.llamadas) == 1


async def test_por_defecto_un_fallo_del_registrador_se_propaga():
    observador = ObservadorDeIntentos(RegistradorQueGuarda(RuntimeError("log caído")))
    with pytest.raises(RuntimeError):
        await _intento(observador)


async def test_con_ante_fallo_el_envio_sigue_con_un_id_de_reemplazo():
    avisos = []
    observador = ObservadorDeIntentos(
        RegistradorQueGuarda(RuntimeError("log caído")), ante_fallo=lambda e, datos: avisos.append(type(e).__name__)
    )
    assert isinstance(await _intento(observador), uuid.UUID)
    assert avisos == ["RuntimeError"] and observador.estado == "pending"


async def test_cerrar_fallido_deja_failed_el_intento_en_pending_y_solo_ese():
    real = RegistradorQueGuarda()
    observador = ObservadorDeIntentos(real)
    await observador.cerrar_fallido("sin intento")  # sin intento en curso no hace nada
    assert real.llamadas == []
    notificacion = await _intento(observador, retry_count=2)
    await observador.cerrar_fallido("plazo_agotado")
    ultimo = real.llamadas[-1]
    assert (ultimo["status"], ultimo["last_error"], ultimo["retry_count"]) == ("failed", "plazo_agotado", 2)
    assert ultimo["notification_id"] == notificacion
    cantidad = len(real.llamadas)
    await observador.cerrar_fallido("otra vez")  # ya está en un estado final
    assert len(real.llamadas) == cantidad
