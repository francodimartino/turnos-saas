"""Pipeline del webhook de Telegram (§2.6) con pool y Telegram simulados: orden, códigos y efectos."""

import hmac
import io
import json
import uuid

import httpx
import pytest

from bot_app import ContextoMensaje, Despachador, crear_app
from bot_app.tests.conftest import PoolFalso, TelegramFalso, update

CABECERA = "X-Telegram-Bot-Api-Secret-Token"
RUTA = "/api/bot/telegram/clave-no-secreta"


def montar(pool, handlers=()):
    telegram = TelegramFalso(pool)
    app = crear_app(pool=pool, despachador=Despachador(handlers), cliente_http=telegram.cliente())
    transporte = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transporte, base_url="http://bot"), telegram


async def enviar(cliente, datos_bot, payload, secret=None):
    _, webhook_secret = datos_bot
    return await cliente.post(RUTA, json=payload, headers={CABECERA: secret or webhook_secret})


async def test_orden_exacto_de_cada_request(pool, datos_bot):
    async def handler(ctx: ContextoMensaje) -> bool:
        ctx.responder("hola")
        return True

    cliente, _ = montar(pool, [handler])
    r = await enviar(cliente, datos_bot, update())
    assert r.status_code == 200
    esperado = [
        "resolver_tenant", "begin", "contexto_tenant", "leer_secret", "dedup",
        "sesion_descartar_o_borrar", "sesion_leer", "commit", "http",
    ]  # fmt: skip
    assert pool.eventos == esperado
    assert pool.contexto == str(pool.tenant_id)


async def test_tenant_desconocido_o_no_activo_responde_200_sin_procesar(datos_bot):
    pool = PoolFalso(None, None)
    llamados = []

    async def handler(ctx):
        llamados.append(1)
        return True

    cliente, telegram = montar(pool, [handler])
    r = await enviar(cliente, datos_bot, update())
    assert r.status_code == 200
    assert pool.eventos == ["resolver_tenant"]  # ni transacción ni contexto ni escritura
    assert llamados == [] and telegram.llamadas == []


@pytest.mark.parametrize("secret", ["incorrecto", ""])
async def test_secret_erroneo_da_401_con_rollback_y_sin_marca(pool, datos_bot, secret):
    llamados = []

    async def handler(ctx):
        llamados.append(1)
        return True

    cliente, telegram = montar(pool, [handler])
    r = await cliente.post(RUTA, json=update(), headers={CABECERA: secret})
    assert r.status_code == 401
    assert pool.eventos == ["resolver_tenant", "begin", "contexto_tenant", "leer_secret", "rollback"]
    assert pool.marcas == set() and llamados == [] and telegram.llamadas == []


async def test_sin_cabecera_da_401(pool, datos_bot):
    cliente, _ = montar(pool)
    r = await cliente.post(RUTA, json=update())
    assert r.status_code == 401
    assert "dedup" not in pool.eventos


async def test_integracion_sin_webhook_secret_da_401(pool, datos_bot):
    pool.secret = json.dumps({"bot_token": datos_bot[0]})
    cliente, _ = montar(pool)
    r = await enviar(cliente, datos_bot, update())
    assert r.status_code == 401
    assert "dedup" not in pool.eventos


async def test_la_comparacion_del_header_es_en_tiempo_constante(pool, datos_bot, monkeypatch):
    usadas = []
    original = hmac.compare_digest
    monkeypatch.setattr(hmac, "compare_digest", lambda a, b: usadas.append(1) or original(a, b))
    cliente, _ = montar(pool)
    await enviar(cliente, datos_bot, update())
    assert usadas


async def test_update_duplicado_se_procesa_una_sola_vez(pool, datos_bot):
    llamados = []

    async def handler(ctx):
        llamados.append(ctx.mensaje.update_id)
        ctx.responder("hola")
        return True

    cliente, telegram = montar(pool, [handler])
    assert (await enviar(cliente, datos_bot, update(7))).status_code == 200
    assert (await enviar(cliente, datos_bot, update(7))).status_code == 200
    assert llamados == ["7"]
    assert len(telegram.llamadas) == 1


async def test_excepcion_al_procesar_hace_rollback_de_la_marca_y_la_reentrega_se_reprocesa(pool, datos_bot):
    estado = {"falla": True}
    llamados = []

    async def handler(ctx):
        llamados.append(ctx.mensaje.update_id)
        if estado["falla"]:
            raise RuntimeError("falla simulada")
        ctx.responder("ok")
        return True

    cliente, telegram = montar(pool, [handler])
    r1 = await enviar(cliente, datos_bot, update(9))
    assert r1.status_code == 500
    assert pool.marcas == set() and telegram.llamadas == []
    assert pool.eventos[-1] == "rollback"
    estado["falla"] = False
    r2 = await enviar(cliente, datos_bot, update(9))
    assert r2.status_code == 200
    assert llamados == ["9", "9"] and len(telegram.llamadas) == 1


async def test_las_respuestas_salen_despues_del_commit_al_chat_correcto(pool, datos_bot):
    async def handler(ctx):
        ctx.responder("hola")
        return True

    cliente, telegram = montar(pool, [handler])
    await enviar(cliente, datos_bot, update(usuario=555))
    assert pool.eventos.index("commit") < pool.eventos.index("http")
    llamada = telegram.llamadas[0]
    assert llamada["url"].endswith("/sendMessage")
    assert llamada["cuerpo"]["chat_id"] == "555" and llamada["cuerpo"]["text"] == "hola"


async def test_sin_handler_responde_el_mensaje_por_defecto(pool, datos_bot):
    cliente, telegram = montar(pool)
    await enviar(cliente, datos_bot, update())
    assert len(telegram.llamadas) == 1


async def test_el_despachador_prueba_los_handlers_en_orden(pool, datos_bot):
    orden = []

    async def primero(ctx):
        orden.append("a")
        return False

    async def segundo(ctx):
        orden.append("b")
        return True

    async def tercero(ctx):
        orden.append("c")
        return True

    cliente, _ = montar(pool, [primero, segundo, tercero])
    await enviar(cliente, datos_bot, update())
    assert orden == ["a", "b"]


async def test_update_no_soportado_responde_200_sin_marca(pool, datos_bot):
    cliente, _ = montar(pool)
    r = await enviar(cliente, datos_bot, {"update_id": 3, "edited_message": {"text": "x"}})
    assert r.status_code == 200
    assert "dedup" not in pool.eventos and pool.marcas == set()


async def test_cuerpo_que_no_es_json_responde_200_sin_marca(pool, datos_bot):
    cliente, _ = montar(pool)
    r = await cliente.post(RUTA, content=b"no es json", headers={CABECERA: datos_bot[1]})
    assert r.status_code == 200
    assert "dedup" not in pool.eventos


async def test_la_sesion_se_persiste_y_se_reutiliza(pool, datos_bot):
    async def handler(ctx):
        ctx.sesion["pasos"] = ctx.sesion.get("pasos", 0) + 1
        return True

    cliente, _ = montar(pool, [handler])
    await enviar(cliente, datos_bot, update(1, usuario=42))
    await enviar(cliente, datos_bot, update(2, usuario=42))
    assert pool.sesiones == {"42": {"pasos": 2}}


async def test_terminar_sesion_la_borra(pool, datos_bot):
    async def handler(ctx):
        if ctx.sesion:
            ctx.terminar_sesion()
        else:
            ctx.sesion["paso"] = "x"
        return True

    cliente, _ = montar(pool, [handler])
    await enviar(cliente, datos_bot, update(1, usuario=42))
    assert "42" in pool.sesiones
    await enviar(cliente, datos_bot, update(2, usuario=42))
    assert pool.sesiones == {}


async def test_health_ok_y_falla(pool, datos_bot):
    cliente, _ = montar(pool)
    assert (await cliente.get("/health")).status_code == 200
    pool.falla_health = True
    assert (await cliente.get("/health")).status_code == 503
    pool.falla_health = False
    pool.rol = "postgres"
    assert (await cliente.get("/health")).status_code == 503


# Datos fijos y sin colisiones posibles con los dígitos buscados (un uuid4 o un token aleatorio pueden contener
# cualquier secuencia, y el filtro de teléfonos puede tapar un uuid con grupos de solo dígitos).
TENANT_FIJO = uuid.UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
DATOS_BOT_FIJOS = ("111:token-de-prueba-abcdef", "secreto-de-webhook-abcdef")


# Repetido 200 veces: antes fallaba de forma intermitente según el uuid aleatorio del tenant.
@pytest.mark.parametrize("repeticion", range(200))
async def test_logs_json_con_tenant_y_sin_datos_del_cliente(repeticion):
    from observabilidad import configurar_logging

    token, webhook_secret = DATOS_BOT_FIJOS
    pool = PoolFalso(TENANT_FIJO, json.dumps({"bot_token": token, "webhook_secret": webhook_secret}))
    salida = io.StringIO()
    configurar_logging(stream=salida)
    cliente, _ = montar(pool)
    await enviar(cliente, DATOS_BOT_FIJOS, update(texto="mi dni es 30111222", usuario=987654321))
    lineas = [json.loads(linea) for linea in salida.getvalue().splitlines() if linea.strip()]
    assert any(linea.get("tenant_id") == str(TENANT_FIJO) for linea in lineas)
    # PII: se revisa todo campo del log salvo el timestamp (no puede contener datos del cliente).
    for linea in lineas:
        contenido = json.dumps({k: v for k, v in linea.items() if k != "timestamp"})
        assert "30111222" not in contenido and "987654321" not in contenido
        assert webhook_secret not in contenido


async def test_update_de_un_grupo_responde_200_sin_handler_ni_escritura(pool, datos_bot):
    llamados = []

    async def handler(ctx):
        llamados.append(1)
        return True

    cliente, telegram = montar(pool, [handler])
    grupo = update()
    grupo["message"]["chat"] = {"id": -100, "type": "supergroup"}
    r = await enviar(cliente, datos_bot, grupo)
    assert r.status_code == 200
    assert llamados == [] and telegram.llamadas == []
    assert "dedup" not in pool.eventos and pool.marcas == set()
