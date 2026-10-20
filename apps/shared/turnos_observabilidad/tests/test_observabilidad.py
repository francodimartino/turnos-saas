"""Tests de observabilidad común (ARCHITECTURE.md §6.1 y §6.3)."""

import io
import json
import logging

import pytest

from turnos_observabilidad import (
    PII_CAMPOS,
    configurar_logging,
    contexto_log,
    iniciar_sentry,
    log_evento_obligatorio,
    sentry_before_send,
)


def _evento_con_pii():
    return {
        "message": "falló el envío",
        "extra": {
            "phone": "+5491155550000",
            "name": "María Pérez",
            "channel_user_id": "123456",
            "text": "hola quiero turno",
            "end_customer_id": "abc",
        },
        "request": {
            "url": "https://x/webhook",
            "data": {"customer": {"phone": "+54911", "name": "Ana"}, "body": "mensaje privado"},
        },
        "breadcrumbs": {
            "values": [
                {"message": "recibido", "data": {"channel_user_id": "99", "message_text": "hola"}},
                {"message": "otro", "data": {"items": [{"phone": "1"}]}},
            ]
        },
    }


def _claves(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _claves(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _claves(v)


def test_before_send_elimina_pii_en_cualquier_nivel():
    salida = sentry_before_send(_evento_con_pii(), {})
    claves = set(_claves(salida))
    for prohibida in ("phone", "name", "channel_user_id", "text", "body", "message_text"):
        assert prohibida not in claves
    assert salida["extra"]["end_customer_id"] == "abc"
    assert salida["request"]["url"] == "https://x/webhook"
    assert salida["message"] == "falló el envío"


def test_before_send_no_muta_el_original():
    original = _evento_con_pii()
    sentry_before_send(original, {})
    assert "phone" in original["extra"]


def test_before_send_ignora_mayusculas():
    salida = sentry_before_send({"extra": {"Phone": "1", "NAME": "x", "ok": 1}}, {})
    assert salida == {"extra": {"ok": 1}}


def test_iniciar_sentry_sin_dsn_no_inicializa(monkeypatch):
    llamadas = []
    monkeypatch.setattr("turnos_observabilidad.sentry.sentry_sdk.init", lambda **kw: llamadas.append(kw))
    assert iniciar_sentry("", "bot") is False
    assert iniciar_sentry(None, "bot") is False
    assert llamadas == []


def test_iniciar_sentry_con_dsn_configura_sin_pii(monkeypatch):
    llamadas = []
    monkeypatch.setattr("turnos_observabilidad.sentry.sentry_sdk.init", lambda **kw: llamadas.append(kw))
    assert iniciar_sentry("https://clave@o0.ingest.sentry.io/1", "turnos-bot") is True
    kw = llamadas[0]
    assert kw["send_default_pii"] is False
    assert kw["before_send"] is sentry_before_send
    assert kw["server_name"] == "turnos-bot"


def _capturar():
    buf = io.StringIO()
    configurar_logging(stream=buf)
    return buf


def test_logging_json_con_contexto():
    buf = _capturar()
    with contexto_log(tenant_id="t-1", actor_type="tenant_user"):
        logging.getLogger("x").info("hola")
    linea = json.loads(buf.getvalue().strip().splitlines()[-1])
    assert linea["message"] == "hola"
    assert linea["level"] == "INFO"
    assert linea["tenant_id"] == "t-1"
    assert linea["actor_type"] == "tenant_user"


def test_logging_sin_contexto_omite_claves():
    buf = _capturar()
    logging.getLogger("x").warning("sin ctx")
    linea = json.loads(buf.getvalue().strip().splitlines()[-1])
    assert "tenant_id" not in linea
    assert "actor_type" not in linea


def test_contexto_se_restaura_al_salir():
    buf = _capturar()
    with contexto_log(tenant_id="t-1"):
        pass
    logging.getLogger("x").info("fuera")
    assert "tenant_id" not in json.loads(buf.getvalue().strip().splitlines()[-1])


def test_logging_incluye_excepcion():
    buf = _capturar()
    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger("x").exception("error")
    linea = json.loads(buf.getvalue().strip().splitlines()[-1])
    assert "ValueError" in linea["exc_info"]


def test_configurar_logging_es_idempotente():
    buf = _capturar()
    configurar_logging(stream=buf)
    logging.getLogger("x").info("una vez")
    assert len(buf.getvalue().strip().splitlines()) == 1


def test_evento_obligatorio_anonymize():
    buf = _capturar()
    log_evento_obligatorio("anonymize_end_customer", end_customer_id="e-1", tenant_id="t-1")
    linea = json.loads(buf.getvalue().strip().splitlines()[-1])
    assert linea["evento"] == "anonymize_end_customer"
    assert linea["end_customer_id"] == "e-1"
    assert linea["tenant_id"] == "t-1"


def test_evento_obligatorio_purga():
    buf = _capturar()
    log_evento_obligatorio("tenant_purge_step", tenant_id="t-1", step="purge_tenant_data")
    linea = json.loads(buf.getvalue().strip().splitlines()[-1])
    assert linea["evento"] == "tenant_purge_step"
    assert linea["step"] == "purge_tenant_data"


def test_evento_obligatorio_rechaza_evento_desconocido():
    with pytest.raises(ValueError):
        log_evento_obligatorio("otra_cosa", tenant_id="t")


@pytest.mark.parametrize("campo", sorted(PII_CAMPOS))
def test_evento_obligatorio_rechaza_pii(campo):
    with pytest.raises(ValueError):
        log_evento_obligatorio("anonymize_end_customer", **{campo: "x"})
