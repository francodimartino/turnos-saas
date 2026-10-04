"""Credencial del bot del tenant: find_integration + get_integration_secret vía platform_secrets (§2.6)."""

import json
import uuid

import pytest
from platform_secrets import AlmacenSecretos, Secreto

from platform_notifications import (
    CredencialInvalida,
    IntegracionNoConfigurada,
    TelegramAdapter,
    adapter_telegram_del_tenant,
    leer_credencial_telegram,
)


class AlmacenFalso(AlmacenSecretos):
    def __init__(self, integracion, valor):
        self.integracion, self.valor, self.buscado = integracion, valor, []

    async def obtener(self, integration_id):
        return None if self.valor is None else Secreto(self.valor)

    async def buscar(self, integration_type, professional_id=None):
        self.buscado.append((integration_type, professional_id))
        return self.integracion

    async def guardar(self, *a, **k): ...

    async def guardar_profesional(self, *a, **k): ...

    async def eliminar_profesional(self, *a, **k): ...


async def test_lee_bot_token_y_webhook_secret(token):
    ws = "ws-" + uuid.uuid4().hex
    almacen = AlmacenFalso(uuid.uuid4(), json.dumps({"bot_token": token, "webhook_secret": ws}))
    cred = await leer_credencial_telegram(almacen)
    assert almacen.buscado == [("telegram_bot", None)]
    assert cred.bot_token.revelar() == token and cred.webhook_secret.revelar() == ws
    assert token not in repr(cred) and ws not in repr(cred)


async def test_sin_integracion_falla_sin_detalle():
    with pytest.raises(IntegracionNoConfigurada):
        await leer_credencial_telegram(AlmacenFalso(None, None))


@pytest.mark.parametrize(
    "valor", ["no es json", "[]", json.dumps({"webhook_secret": "x"}), json.dumps({"bot_token": ""})]
)
async def test_secret_malformado_no_filtra_su_contenido(valor):
    with pytest.raises(CredencialInvalida) as exc:
        await leer_credencial_telegram(AlmacenFalso(uuid.uuid4(), valor))
    assert valor not in str(exc.value)
    assert exc.value.__cause__ is None


async def test_adapter_del_tenant_usa_el_token_del_secret(token):
    almacen = AlmacenFalso(uuid.uuid4(), json.dumps({"bot_token": token, "webhook_secret": "w"}))
    adapter = await adapter_telegram_del_tenant(almacen, cliente=None)
    assert isinstance(adapter, TelegramAdapter)
