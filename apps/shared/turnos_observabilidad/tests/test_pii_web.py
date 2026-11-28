"""Campos de la web pública (email, código de verificación, token de sesión, IP) en logs y Sentry."""

import io
import json
import logging

import pytest

from turnos_observabilidad import PII_CAMPOS, configurar_logging, sentry_before_send
from turnos_observabilidad.logs import CAMPOS_EXTRA_PERMITIDOS
from turnos_observabilidad.pii import limpiar_texto

CAMPOS_WEB = ("email", "codigo", "token", "ip")


@pytest.mark.parametrize("campo", CAMPOS_WEB)
def test_los_campos_de_la_web_son_pii(campo):
    assert campo in PII_CAMPOS


@pytest.mark.parametrize("campo", CAMPOS_WEB)
def test_ningun_campo_pii_de_la_web_esta_permitido_como_extra_de_log(campo):
    # El código de error de una respuesta se loguea como `codigo_error`; `codigo` es el de verificación.
    assert campo not in CAMPOS_EXTRA_PERMITIDOS


def test_el_codigo_de_error_tiene_su_propio_campo_de_log():
    assert "codigo_error" in CAMPOS_EXTRA_PERMITIDOS


@pytest.mark.parametrize("campo", CAMPOS_WEB)
def test_sentry_quita_los_campos_de_la_web_en_cualquier_nivel(campo):
    evento = {"extra": {campo: "valor-secreto"}, "request": {"data": {"anidado": [{campo.upper(): "valor-secreto"}]}}}
    assert "valor-secreto" not in json.dumps(sentry_before_send(evento))


def test_los_logs_no_emiten_los_campos_de_la_web_ni_con_extra():
    buf = io.StringIO()
    configurar_logging(stream=buf)
    logging.getLogger("t").info(
        "evento", extra={"email": "ana@ejemplo.test", "codigo": "123456", "token": "abc", "ip": "203.0.113.9"}
    )
    salida = buf.getvalue()
    for secreto in ("ana@ejemplo.test", "123456", "abc", "203.0.113.9"):
        assert secreto not in salida


@pytest.mark.parametrize(
    ("texto", "secreto"),
    [
        ("email=ana@ejemplo.test&x=1", "ana@ejemplo.test"),
        ("?codigo=123456&y=2", "123456"),
        ("?x=1&ip=203.0.113.9&y=2", "203.0.113.9"),
    ],
)
def test_las_querystrings_con_email_codigo_o_ip_se_filtran(texto, secreto):
    assert secreto not in limpiar_texto(texto)


def test_el_texto_que_solo_nombra_codigo_no_se_toca():
    assert limpiar_texto("codigo de error: no_disponible") == "codigo de error: no_disponible"


@pytest.mark.parametrize(
    "texto",
    [
        "Key (email)=(ana@ejemplo.test) already exists.",
        "falló para ana.perez+reservas@sub.ejemplo.test, reintentar",
        'mailto:ana@ejemplo.test?subject=x "ana@ejemplo.test"',
    ],
)
def test_un_email_dentro_de_un_texto_se_filtra(texto):
    assert "ejemplo.test" not in limpiar_texto(texto)


def test_el_filtro_de_emails_es_lineal_en_textos_largos():
    import time

    inicio = time.monotonic()
    limpiar_texto("a" * 300_000 + " " + "b@" * 100_000)
    assert time.monotonic() - inicio < 2


def test_un_texto_sin_email_conserva_su_forma():
    assert limpiar_texto("usuario@ sin dominio y @mención") == "usuario@ sin dominio y @mención"


def test_una_clave_que_solo_contiene_ip_no_se_filtra():
    assert limpiar_texto("?tip=ver&zip=5000") == "?tip=ver&zip=5000"
