"""El email como canal de la política de reintentos: qué es permanente y qué se reintenta."""

import pytest
from platform_notifications import ErrorEnvio
from turnos_email import ErrorEmail

from enviador_app.canales import AdaptadorEmail


class ClienteQueFalla:
    def __init__(self, error):
        self.error = error
        self.llamadas = []

    async def enviar(self, destino, asunto, texto, html=None):
        self.llamadas.append((destino, asunto, texto))
        if self.error is not None:
            raise self.error


async def test_envia_a_la_direccion_con_el_asunto_y_el_cuerpo():
    cliente = ClienteQueFalla(None)
    await AdaptadorEmail(cliente, "Tu turno fue confirmado").send_message("ana@ejemplo.test", "Cuerpo")
    assert cliente.llamadas == [("ana@ejemplo.test", "Tu turno fue confirmado", "Cuerpo")]


async def test_los_botones_se_ignoran_y_sale_el_texto():
    cliente = ClienteQueFalla(None)
    await AdaptadorEmail(cliente, "Asunto").send_buttons("ana@ejemplo.test", "Cuerpo", [])
    assert cliente.llamadas == [("ana@ejemplo.test", "Asunto", "Cuerpo")]


@pytest.mark.parametrize(
    ("estado", "reintentable"),
    [(None, True), (429, True), (500, True), (503, True), (400, False), (401, False), (422, False)],
)
async def test_el_error_del_proveedor_se_traduce_con_su_estado(estado, reintentable):
    cliente = ClienteQueFalla(ErrorEmail(estado_http=estado, retry_after=3.0 if estado == 429 else None))
    with pytest.raises(ErrorEnvio) as info:
        await AdaptadorEmail(cliente, "Asunto").send_message("ana@ejemplo.test", "Cuerpo con datos")
    assert info.value.reintentable is reintentable
    assert info.value.estado_http == estado
    assert info.value.retry_after == (3.0 if estado == 429 else None)
    assert "ana@ejemplo.test" not in str(info.value) and "Cuerpo con datos" not in str(info.value)


async def test_un_error_que_no_es_del_cliente_de_email_se_propaga_tal_cual():
    with pytest.raises(RuntimeError):
        await AdaptadorEmail(ClienteQueFalla(RuntimeError("x")), "Asunto").send_message("a@b.test", "c")
