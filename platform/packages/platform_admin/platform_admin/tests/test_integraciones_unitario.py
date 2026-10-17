"""Tests unitarios de la carga de credenciales de integraciones de tenant."""

import json
import logging
import secrets
import uuid

import pytest
from platform_secrets import Secreto

from platform_admin import (
    BotYaConectado,
    DatosInvalidos,
    IntegracionNoSoportada,
    TenantNoEncontrado,
    cargar_integracion_tenant,
)
from platform_admin.tests.falsos import ConexionFalsa, PoolFalso

ACTOR = uuid.UUID("22222222-2222-2222-2222-222222222222")
TENANT = uuid.UUID("33333333-3333-3333-3333-333333333333")
INTEGRACION = uuid.UUID("44444444-4444-4444-4444-444444444444")


def _conn(existente=None, tenant=TENANT, bot_en_otro=None):
    return ConexionFalsa(
        {
            "select id from public.tenants": tenant,
            "config ->> 'bot_id'": bot_en_otro,
            "from public.tenant_integrations": existente,
            "public.store_tenant_integration": INTEGRACION,
        }
    )


def _secreto():
    return json.dumps({"bot_token": f"123456:{secrets.token_urlsafe(24)}", "webhook_secret": secrets.token_urlsafe(32)})


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


_TOKEN_OK = "123456:ABC-def_GHI"
_WEBHOOK_OK = "secreto_OK-123"  # corto a propósito: solo se usa en credenciales inválidas


@pytest.mark.parametrize(
    "secreto",
    [
        "franco",
        "[]",
        "null",
        "5",
        json.dumps({"bot_token": _TOKEN_OK}),
        json.dumps({"webhook_secret": _WEBHOOK_OK}),
        json.dumps({"bot_token": 5, "webhook_secret": _WEBHOOK_OK}),
        json.dumps({"bot_token": "sin-dos-puntos", "webhook_secret": _WEBHOOK_OK}),
        json.dumps({"bot_token": "123:ab cd", "webhook_secret": _WEBHOOK_OK}),
        json.dumps({"bot_token": _TOKEN_OK, "webhook_secret": ""}),
        json.dumps({"bot_token": _TOKEN_OK, "webhook_secret": "con espacio"}),
        json.dumps({"bot_token": _TOKEN_OK, "webhook_secret": "a" * 257}),
        json.dumps({"bot_token": _TOKEN_OK, "webhook_secret": "a" * 31}),
        json.dumps({"bot_token": _TOKEN_OK, "webhook_secret": 7}),
    ],
)
async def test_credencial_de_telegram_invalida_se_rechaza_sin_tocar_la_base_ni_nombrar_el_valor(secreto):
    pool = PoolFalso()
    with pytest.raises(DatosInvalidos) as exc:
        await _cargar(pool, {}, secreto)
    assert pool.usadas == []
    mensaje = str(exc.value)
    assert len(secreto) < 4 or secreto not in mensaje
    assert "franco" not in mensaje
    assert _TOKEN_OK not in mensaje and _WEBHOOK_OK not in mensaje


@pytest.mark.parametrize("secreto", ["[" * 100000, '{"a":' * 100000], ids=["corchetes", "objetos"])
async def test_credencial_de_telegram_con_anidamiento_extremo_es_datos_invalidos_sin_el_valor(secreto):
    pool = PoolFalso()
    with pytest.raises(DatosInvalidos) as exc:
        await _cargar(pool, {}, secreto)
    assert pool.usadas == []
    assert secreto[:50] not in str(exc.value)
    assert exc.value.__cause__ is None and exc.value.__suppress_context__


async def test_credencial_de_telegram_invalida_envuelta_en_secreto_tambien_se_rechaza():
    pool = PoolFalso()
    with pytest.raises(DatosInvalidos):
        await _cargar(pool, {}, Secreto("franco"))
    assert pool.usadas == []


async def test_el_mensaje_dice_el_formato_esperado():
    with pytest.raises(DatosInvalidos, match="bot_token"):
        await _cargar(PoolFalso(), {}, "franco")
    with pytest.raises(DatosInvalidos, match="webhook_secret"):
        await _cargar(PoolFalso(), {}, json.dumps({"bot_token": _TOKEN_OK}))


async def test_credencial_de_telegram_valida_con_webhook_secret_de_256_se_guarda():
    conn = _conn()
    secreto = json.dumps({"bot_token": _TOKEN_OK, "webhook_secret": "a" * 256})
    await _cargar(PoolFalso(conn), {}, secreto)
    assert conn.con("public.store_tenant_integration")[0][1][3] == secreto


async def test_credencial_de_telegram_con_webhook_secret_de_32_se_guarda():
    conn = _conn()
    secreto = json.dumps({"bot_token": _TOKEN_OK, "webhook_secret": "a-_Z" * 8})
    await _cargar(PoolFalso(conn), {}, secreto)
    assert conn.con("public.store_tenant_integration")


async def test_el_mensaje_de_webhook_secret_dice_32_a_256_sin_repetir_el_valor():
    corto = "c" * 31
    with pytest.raises(DatosInvalidos) as exc:
        await _cargar(PoolFalso(), {}, json.dumps({"bot_token": _TOKEN_OK, "webhook_secret": corto}))
    assert "32 a 256" in str(exc.value) and corto not in str(exc.value)


async def test_guarda_el_bot_id_numerico_y_pisa_el_que_mande_el_cliente():
    conn = _conn()
    secreto = json.dumps({"bot_token": "987654:ABC-def_GHI", "webhook_secret": "w" * 40})
    await _cargar(PoolFalso(conn), {"bot_id": "111", "bot_username": "x"}, secreto)
    config = json.loads(conn.con("public.store_tenant_integration")[0][1][2])
    assert config["bot_id"] == "987654" and config["bot_username"] == "x"
    assert "ABC-def_GHI" not in json.dumps(config)


async def test_el_mismo_bot_en_otro_tenant_da_bot_ya_conectado_sin_guardar_ni_identificar_al_otro():
    otro = uuid.UUID("55555555-5555-5555-5555-555555555555")
    conn = _conn(bot_en_otro=1)
    secreto = json.dumps({"bot_token": "987654:ABC-def_GHI", "webhook_secret": "w" * 40})
    with pytest.raises(BotYaConectado) as exc:
        await _cargar(PoolFalso(conn), {}, secreto)
    assert not conn.con("public.store_tenant_integration")
    assert str(otro) not in str(exc.value) and "987654" not in str(exc.value)
    assert conn.con("config ->> 'bot_id'")[0][1][1:] == ("987654", TENANT)


async def test_una_carrera_con_el_indice_unico_tambien_da_bot_ya_conectado():
    class Violacion(Exception):
        sqlstate = "23505"

    class ConnCarrera(ConexionFalsa):
        async def fetchval(self, sql, *args):
            if "public.store_tenant_integration" in sql:
                raise Violacion()
            return await super().fetchval(sql, *args)

    conn = ConnCarrera({"select id from public.tenants": TENANT})
    secreto = json.dumps({"bot_token": "987654:ABC-def_GHI", "webhook_secret": "w" * 40})
    with pytest.raises(BotYaConectado):
        await _cargar(PoolFalso(conn), {}, secreto)
