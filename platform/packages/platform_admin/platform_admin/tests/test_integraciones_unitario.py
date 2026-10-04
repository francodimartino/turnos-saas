"""Tests unitarios de la carga de credenciales de integraciones de tenant."""

import json
import logging
import secrets
import uuid

import pytest
from platform_secrets import Secreto

from platform_admin import (
    DatosInvalidos,
    IntegracionNoSoportada,
    TenantNoEncontrado,
    cargar_integracion_tenant,
)
from platform_admin.tests.falsos import ConexionFalsa, PoolFalso

ACTOR = uuid.UUID("22222222-2222-2222-2222-222222222222")
TENANT = uuid.UUID("33333333-3333-3333-3333-333333333333")
INTEGRACION = uuid.UUID("44444444-4444-4444-4444-444444444444")


def _conn(existente=None, tenant=TENANT):
    return ConexionFalsa(
        {
            "select id from public.tenants": tenant,
            "from public.tenant_integrations": existente,
            "public.store_tenant_integration": INTEGRACION,
        }
    )


def _secreto():
    return json.dumps({"bot_token": secrets.token_urlsafe(24), "webhook_secret": secrets.token_urlsafe(24)})


async def _cargar(pool, config, secreto, tipo="telegram_bot"):
    return await cargar_integracion_tenant(pool, TENANT, tipo, config, secreto, actor_id=ACTOR)


async def test_telegram_llama_a_store_tenant_integration_y_genera_webhook_key_uuid():
    conn, secreto = _conn(), _secreto()
    res = await _cargar(PoolFalso(conn), {"bot_username": "x"}, secreto)

    _, args = conn.con("public.store_tenant_integration")[0]
    assert args[0] == TENANT and args[1] == "telegram_bot" and args[3] == secreto
    config = json.loads(args[2])
    assert config["bot_username"] == "x"
    assert str(uuid.UUID(config["webhook_key"])) == config["webhook_key"]
    assert res.integration_id == INTEGRACION and res.webhook_key == config["webhook_key"]
    assert conn.sqls()[0] == "BEGIN" and conn.sqls()[-1] == "COMMIT"


async def test_el_secret_nunca_aparece_en_la_respuesta_ni_en_los_logs(caplog):
    conn, secreto = _conn(), _secreto()
    with caplog.at_level(logging.DEBUG):
        res = await _cargar(PoolFalso(conn), {}, Secreto(secreto))
    for texto in (repr(res), str(res), json.dumps(res.__dict__, default=str), caplog.text):
        assert secreto not in texto
    assert not hasattr(res, "secret")


async def test_si_ya_hay_webhook_key_en_config_se_respeta():
    clave = str(uuid.uuid4())
    conn = _conn()
    await _cargar(PoolFalso(conn), {"webhook_key": clave}, _secreto())
    assert json.loads(conn.con("public.store_tenant_integration")[0][1][2])["webhook_key"] == clave


async def test_al_recargar_una_integracion_existente_conserva_su_webhook_key():
    clave = str(uuid.uuid4())
    conn = _conn(existente=json.dumps({"webhook_key": clave}))
    res = await _cargar(PoolFalso(conn), {}, _secreto())
    assert res.webhook_key == clave


@pytest.mark.parametrize(
    ("tipo", "texto"),
    [
        ("google_calendar", "por-profesional"),
        ("whatsapp_business", "piloto"),
        ("mercadopago", "no se carga"),
        ("otra_cosa", "no se carga"),
    ],
)
async def test_solo_telegram_bot_se_carga_y_el_resto_se_rechaza_con_error_claro(tipo, texto):
    pool = PoolFalso()
    with pytest.raises(IntegracionNoSoportada, match=texto):
        await _cargar(pool, {}, _secreto(), tipo)
    assert pool.usadas == []


async def test_error_de_tipo_no_incluye_el_secret():
    secreto = _secreto()
    with pytest.raises(IntegracionNoSoportada) as exc:
        await _cargar(PoolFalso(), {}, secreto, "whatsapp_business")
    assert secreto not in str(exc.value)


@pytest.mark.parametrize(
    "config",
    [
        {"bot_token": "x"},
        {"webhook_secret": "x"},
        {"anidado": {"token": "x"}},
        {"webhook_key": "no-es-uuid"},
        {"webhook_key": 5},
        "texto",
        None,
    ],
)
async def test_config_invalida_o_con_claves_de_secret_se_rechaza_sin_tocar_la_base(config):
    pool = PoolFalso()
    with pytest.raises(DatosInvalidos):
        await _cargar(pool, config, _secreto())
    assert pool.usadas == []


@pytest.mark.parametrize("secreto", ["", None, 5, Secreto("")])
async def test_secret_vacio_o_de_tipo_invalido_se_rechaza(secreto):
    pool = PoolFalso()
    with pytest.raises(DatosInvalidos):
        await _cargar(pool, {}, secreto)
    assert pool.usadas == []


async def test_tenant_inexistente():
    with pytest.raises(TenantNoEncontrado):
        await _cargar(PoolFalso(_conn(tenant=None)), {}, _secreto())
