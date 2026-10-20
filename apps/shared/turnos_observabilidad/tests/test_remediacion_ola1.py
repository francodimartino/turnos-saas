"""Sentry sin variables locales, lista blanca de campos de log y patrones de secretos del stack."""

import ast
import io
import json
import logging
from pathlib import Path
from unittest.mock import patch

import sentry_sdk
from sentry_sdk.transport import Transport

from turnos_observabilidad import configurar_logging, iniciar_sentry
from turnos_observabilidad.logs import CAMPOS_EXTRA_PERMITIDOS
from turnos_observabilidad.pii import limpiar_texto, limpiar_valor

RAIZ = Path(__file__).resolve().parents[4]
CLAVE = "S3cr3t0-Contrasena-Unica"
TOKEN = "987654321:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
WEBHOOK = "whsec-unico-12345"


def test_iniciar_sentry_desactiva_variables_locales():
    with patch("turnos_observabilidad.sentry.sentry_sdk.init") as init:
        assert iniciar_sentry("https://abc@o1.ingest.sentry.io/1", "svc") is True
    assert init.call_args.kwargs["include_local_variables"] is False


def test_evento_real_no_contiene_variables_locales_sensibles():
    eventos: list[dict] = []

    class Falso(Transport):
        def capture_envelope(self, envelope):
            for item in envelope.items:
                if item.payload.json is not None and item.headers.get("type") == "event":
                    eventos.append(item.payload.json)

    def handler():
        contrasena = CLAVE  # noqa: F841
        credencial = {"bot_token": TOKEN, "webhook_secret": WEBHOOK}  # noqa: F841
        raise RuntimeError("fallo")

    with patch("turnos_observabilidad.sentry.sentry_sdk.init", wraps=sentry_sdk.init) as init:
        iniciar_sentry("https://" + "0123456789abcdef" * 2 + "@o1.ingest.sentry.io/1", "svc")
    kwargs = dict(init.call_args.kwargs)
    kwargs["transport"] = Falso()
    sentry_sdk.init(**kwargs)
    try:
        try:
            handler()
        except RuntimeError as e:
            sentry_sdk.capture_exception(e)
        sentry_sdk.flush()
    finally:
        sentry_sdk.init()
    assert eventos
    serializado = json.dumps(eventos)
    for secreto in (CLAVE, TOKEN, WEBHOOK):
        assert secreto not in serializado


def _log(**extra) -> dict:
    buf = io.StringIO()
    configurar_logging(stream=buf)
    logging.getLogger("t").info("hola", extra=extra)
    return json.loads(buf.getvalue().strip().splitlines()[-1])


def test_formateador_descarta_campos_fuera_de_la_lista_blanca():
    d = _log(password="x", bot_token=TOKEN, nombre="Ana")
    assert not {"password", "bot_token", "nombre"} & set(d)
    assert TOKEN not in json.dumps(d)


def test_formateador_conserva_campos_de_la_lista_blanca():
    d = _log(metodo="GET", ruta="/x", estado=200, duracion_ms=1.5, codigo="ok")
    assert (d["metodo"], d["ruta"], d["estado"], d["duracion_ms"], d["codigo"]) == ("GET", "/x", 200, 1.5, "ok")


def _claves_usadas() -> set[tuple[str, str]]:
    usadas = set()
    for ruta in RAIZ.rglob("*.py"):
        partes = ruta.relative_to(RAIZ).parts
        if any(p in {"tests", ".venv", "node_modules"} or p.startswith(".") for p in partes):
            continue
        if ruta.name.startswith("test_") or ruta.name.startswith("soporte"):
            continue
        arbol = ast.parse(ruta.read_text(encoding="utf-8"))
        for nodo in ast.walk(arbol):
            if not isinstance(nodo, ast.Call):
                continue
            for kw in nodo.keywords:
                if kw.arg == "extra" and isinstance(kw.value, ast.Dict):
                    for k in kw.value.keys:
                        if isinstance(k, ast.Constant):
                            usadas.add((str(ruta), k.value))
            nombre = getattr(nodo.func, "id", getattr(nodo.func, "attr", ""))
            if nombre == "log_evento_obligatorio":
                usadas |= {(str(ruta), kw.arg) for kw in nodo.keywords if kw.arg}
    return usadas


def test_ningun_log_del_repo_usa_claves_fuera_de_la_lista():
    usadas = _claves_usadas()
    assert usadas, "el recorrido no encontró ningún logger.*(extra=...)"
    fuera = sorted((r, k) for r, k in usadas if k not in CAMPOS_EXTRA_PERMITIDOS)
    assert not fuera, f"claves de log fuera de CAMPOS_EXTRA_PERMITIDOS: {fuera}"


def test_patrones_del_stack_se_filtran():
    jwt = "ey" + "JhbGciOiJIUzI1NiJ9.ey" + "JzdWIiOiIxMjMifQ.firma_abc-123"
    assert "eyJ" not in limpiar_texto(f"Authorization {jwt} fin")
    out = limpiar_texto("postgres" + "ql://usuario:CLAVE123@db.host.com:5432/db")
    assert "CLAVE123" not in out and "usuario" in out and "db.host.com" in out
    for crudo in (
        "https://" + "0123456789abcdef" * 2 + "@o123.ingest.sentry.io/456",
        "GOC" + "SPX-abcDEF_123-xyz",
        "APP" + "_USR-1234567890-abcdef-9876",
    ):
        assert limpiar_texto(f"x {crudo} y") == "x [filtrado] y"
    for texto in (
        json.dumps({"bot_token": TOKEN, "webhook_secret": WEBHOOK}),
        str({"bot_token": TOKEN, "webhook_secret": WEBHOOK}),
        json.dumps(json.dumps({"bot_token": TOKEN})),
    ):
        out = limpiar_texto(texto)
        assert TOKEN not in out and WEBHOOK not in out and "AAHdq" not in out


def test_claves_en_espanol_se_eliminan_en_cualquier_nivel():
    interno = {"password": "p", "telefono": "1", "contrasena": "c", "ok": 1}
    sucio = {"a": [{"nombre": "Ana", "email": "a@b.c", "x": interno}]}
    assert limpiar_valor(sucio) == {"a": [{"x": {"ok": 1}}]}


def test_bordes():
    assert "1155550000" not in limpiar_texto("1155550000a")
    assert "SECRETO" not in limpiar_texto("xaccess_token=SECRETO")
    assert "SECRETO" not in limpiar_texto('{"xtoken":"SECRETO"}')
