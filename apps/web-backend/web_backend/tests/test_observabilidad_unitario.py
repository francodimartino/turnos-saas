"""Observabilidad de la web pública: Sentry propio y ningún dato personal en logs ni en Sentry."""

import ast
import io
import json
import logging
import secrets
from pathlib import Path

import pytest
import sentry_sdk
from sentry_sdk.transport import Transport
from turnos_observabilidad import configurar_logging
from turnos_observabilidad.logs import CAMPOS_EXTRA_PERMITIDOS

from web_backend import app as modulo_app
from web_backend.tests.soporte import PoolFalso, app_de_prueba, config_prueba, http

PAQUETE = Path(modulo_app.__file__).resolve().parent

# Lo que una persona manda a la web o recibe de ella: ninguno de estos valores puede salir por logs ni por Sentry.
EMAIL = "ana.perez@ejemplo.test"
CODIGO = "493817"
TOKEN = "sesion-" + secrets.token_hex(6)  # distinto en cada corrida: no es una credencial real
IP = "203.0.113.77"
NOMBRE = "Ana Perez Gomez"
TELEFONO = "+54 9 11 5555-0123"
SECRETOS = (EMAIL, CODIGO, TOKEN, IP, NOMBRE, "Perez", "5555-0123")


@pytest.fixture
def logs():
    """Deja los logs de la app en un buffer JSON y restaura el logger raíz al terminar."""
    raiz = logging.getLogger()
    antes = list(raiz.handlers)
    nivel = raiz.level
    buf = io.StringIO()
    configurar_logging(stream=buf)
    yield buf
    raiz.handlers = antes
    raiz.setLevel(nivel)


class _TransporteEnMemoria(Transport):
    """No envía nada: guarda los eventos que Sentry habría mandado."""

    def __init__(self, destino: list[dict]):
        super().__init__()
        self._destino = destino

    def capture_envelope(self, envelope):
        evento = envelope.get_event()
        if evento is not None:
            self._destino.append(evento)


@pytest.fixture
def eventos_de_sentry(monkeypatch):
    """Inicia Sentry como lo hace la app (`iniciar_sentry`) pero con un transporte que guarda cada evento."""
    eventos: list[dict] = []
    original = sentry_sdk.init
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: original(**kw, transport=_TransporteEnMemoria(eventos)))
    yield eventos
    sentry_sdk.flush(timeout=2)
    sentry_sdk.get_global_scope().set_client(None)


def _sin_secretos(texto: str) -> None:
    for secreto in SECRETOS:
        assert secreto not in texto, f"salió {secreto!r}"


# --- Sentry: proyecto del servicio -------------------------------------------------------------------------------


def test_crear_app_inicia_sentry_con_el_dsn_del_servicio(monkeypatch):
    llamadas: list[tuple] = []
    monkeypatch.setattr(modulo_app, "iniciar_sentry", lambda dsn, servicio: llamadas.append((dsn, servicio)))
    monkeypatch.setattr(modulo_app, "configurar_logging", lambda: None)
    dsn = "https://clave@o1.ingest.sentry.io/42"
    modulo_app.crear_app(config_prueba(sentry_dsn=dsn), pool=PoolFalso())
    assert llamadas == [(dsn, "web-backend")]


def test_sin_dsn_sentry_queda_apagado():
    from turnos_observabilidad import iniciar_sentry

    assert iniciar_sentry(None, "web-backend") is False


def test_el_servicio_se_llama_como_su_proyecto_de_sentry():
    assert modulo_app.SERVICIO == "web-backend"


# --- Logs ------------------------------------------------------------------------------------------------------


async def test_el_log_de_la_peticion_lleva_ruta_y_estado_pero_no_la_query(logs):
    app, _ = app_de_prueba()
    consulta = f"?email={EMAIL}&codigo={CODIGO}&token={TOKEN}&ip={IP}"
    r = await http(app).get("/negocios/sol" + consulta, headers={"X-Forwarded-For": IP, "Authorization": TOKEN})
    assert r.status_code == 200
    salida = logs.getvalue()
    assert '"message": "peticion"' in salida and '"ruta": "/negocios/sol"' in salida and '"estado": 200' in salida
    _sin_secretos(salida)


async def test_un_error_de_la_base_no_deja_datos_de_la_persona_en_los_logs(logs):
    pool = PoolFalso()
    pool.conn.error = ConnectionRefusedError(f"fallo con {EMAIL} codigo={CODIGO} token={TOKEN}")
    app, _ = app_de_prueba(pool=pool)
    r = await http(app).get(f"/negocios/sol?email={EMAIL}&codigo={CODIGO}")
    assert r.status_code == 503
    _sin_secretos(logs.getvalue())
    _sin_secretos(r.text)


def test_un_log_con_campos_de_la_persona_los_descarta(logs):
    logging.getLogger("web_backend").warning(
        "evento",
        extra={"email": EMAIL, "codigo": CODIGO, "token": TOKEN, "ip": IP, "nombre": NOMBRE, "telefono": TELEFONO},
    )
    _sin_secretos(logs.getvalue())
    assert "5555" not in logs.getvalue()
    assert "evento" in logs.getvalue()


def _claves_extra_de_logs() -> set[str]:
    claves = set()
    for ruta in PAQUETE.rglob("*.py"):
        if "tests" in ruta.parts:
            continue
        for nodo in ast.walk(ast.parse(ruta.read_text(encoding="utf-8"))):
            if isinstance(nodo, ast.Call):
                for kw in nodo.keywords:
                    if kw.arg == "extra" and isinstance(kw.value, ast.Dict):
                        claves |= {k.value for k in kw.value.keys if isinstance(k, ast.Constant)}
    return claves


def test_las_claves_de_log_del_servicio_estan_en_la_lista_permitida():
    usadas = _claves_extra_de_logs()
    assert usadas, "el servicio no loguea nada con extra"
    assert usadas <= CAMPOS_EXTRA_PERMITIDOS, usadas - CAMPOS_EXTRA_PERMITIDOS


def test_ningun_log_del_servicio_usa_una_clave_de_pii():
    from turnos_observabilidad import PII_CAMPOS

    assert not _claves_extra_de_logs() & {c.lower() for c in PII_CAMPOS}


# --- Sentry ----------------------------------------------------------------------------------------------------


async def test_una_excepcion_no_manejada_no_deja_datos_de_la_persona_en_sentry_ni_en_los_logs(eventos_de_sentry, logs):
    modulo_app.iniciar_sentry("http://clave@localhost:9/1", modulo_app.SERVICIO)
    # El nombre viaja bajo claves de PII (extras y contexto del evento): el filtro lo descarta. En texto libre no se
    # puede detectar (ver `test_un_nombre_en_texto_libre_no_se_filtra`), por eso el mensaje no lo lleva.
    sentry_sdk.get_current_scope().set_extra("cliente", {"nombre": NOMBRE, "email": EMAIL})
    sentry_sdk.get_current_scope().set_context("persona", {"name": NOMBRE, "phone": TELEFONO})
    pool = PoolFalso()
    pool.error_al_adquirir = RuntimeError(f"fallo con {EMAIL} y {TELEFONO} codigo={CODIGO} token={TOKEN}")
    app, _ = app_de_prueba(pool=pool)
    consulta = f"?email={EMAIL}&codigo={CODIGO}&token={TOKEN}&ip={IP}"
    r = await http(app).get("/negocios/sol" + consulta, headers={"X-Forwarded-For": IP, "Authorization": TOKEN})
    assert r.status_code == 500
    sentry_sdk.flush(timeout=2)
    assert eventos_de_sentry, "la excepción no llegó a Sentry"
    texto = json.dumps(eventos_de_sentry, default=str)
    _sin_secretos(texto)  # incluye el nombre
    _sin_secretos(logs.getvalue())
    assert "5555" not in texto and "5555" not in logs.getvalue()
    assert '"estado": 500' in logs.getvalue()


async def test_sentry_arranca_sin_datos_personales_por_defecto(eventos_de_sentry):
    modulo_app.iniciar_sentry("http://clave@localhost:9/1", modulo_app.SERVICIO)
    cliente = sentry_sdk.get_client()
    assert cliente.options["send_default_pii"] is False
    assert cliente.options["include_local_variables"] is False
    assert cliente.options["server_name"] == "web-backend"


async def test_el_filtro_de_sentry_quita_email_codigo_token_ip_y_datos_del_cliente(eventos_de_sentry):
    modulo_app.iniciar_sentry("http://clave@localhost:9/1", modulo_app.SERVICIO)
    with sentry_sdk.new_scope() as scope:
        scope.set_extra("datos", {"email": EMAIL, "codigo": CODIGO, "token": TOKEN, "ip": IP, "name": NOMBRE})
        scope.set_context("cliente", {"phone": TELEFONO, "nombre": NOMBRE, "anidado": [{"Email": EMAIL}]})
        scope.set_tag("email", EMAIL)
        sentry_sdk.capture_message(f"acceso pedido por {EMAIL}?codigo={CODIGO}&token={TOKEN}&ip={IP}")
    sentry_sdk.flush(timeout=2)
    assert eventos_de_sentry
    texto = json.dumps(eventos_de_sentry, default=str)
    _sin_secretos(texto)  # incluye NOMBRE: por las claves `name` y `nombre`
    assert "5555-0123" not in texto


async def test_un_nombre_en_texto_libre_no_se_filtra_por_diseno(eventos_de_sentry):
    """Limitación documentada (pii.py y README): un nombre dentro de un tag o un mensaje no se puede detectar.

    Por eso el servicio no pone datos de la persona en texto libre (ver `test_el_codigo_no_pasa_datos_a_texto_libre`).
    """
    modulo_app.iniciar_sentry("http://clave@localhost:9/1", modulo_app.SERVICIO)
    with sentry_sdk.new_scope() as scope:
        scope.set_tag("cliente", NOMBRE)
        sentry_sdk.capture_message("evento")
    sentry_sdk.flush(timeout=2)
    assert NOMBRE in json.dumps(eventos_de_sentry, default=str)


# Llamadas que ponen texto libre en Sentry o en los logs.
LLAMADAS_DE_TEXTO_LIBRE = {
    "set_tag",
    "set_tags",
    "set_extra",
    "set_context",
    "set_user",
    "capture_message",
    "add_breadcrumb",
}
METODOS_DE_LOG = {"debug", "info", "warning", "error", "exception", "critical"}


def test_el_codigo_no_pasa_datos_a_texto_libre():
    """Sobre el código real: ningún tag, contexto ni mensaje, y los mensajes de log son literales (sin formato)."""
    for ruta in PAQUETE.rglob("*.py"):
        if "tests" in ruta.parts:
            continue
        for nodo in ast.walk(ast.parse(ruta.read_text(encoding="utf-8"))):
            if not (isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Attribute)):
                continue
            nombre = nodo.func.attr
            assert nombre not in LLAMADAS_DE_TEXTO_LIBRE, f"{ruta.name}:{nodo.lineno} usa {nombre}"
            if nombre in METODOS_DE_LOG and isinstance(nodo.func.value, ast.Name) and nodo.func.value.id == "_log":
                primero = nodo.args[0]
                assert isinstance(primero, ast.Constant) and isinstance(primero.value, str), (
                    f"{ruta.name}:{nodo.lineno}: el mensaje de log tiene que ser un literal"
                )
                assert len(nodo.args) == 1, f"{ruta.name}:{nodo.lineno}: sin argumentos de formato en el log"


def test_el_detector_de_texto_libre_marca_un_log_con_formato():
    arbol = ast.parse("_log.warning(f'fallo {x}')" + chr(10) + "_log.info('ok %s', y)")
    llamadas = [n for n in ast.walk(arbol) if isinstance(n, ast.Call)]
    literales = [isinstance(n.args[0], ast.Constant) and len(n.args) == 1 for n in llamadas]
    assert literales == [False, False]


# --- uvicorn: access log -----------------------------------------------------------------------------------------


def test_el_servicio_apaga_el_access_log_de_uvicorn_al_crear_la_app():
    acceso = logging.getLogger("uvicorn.access")
    acceso.disabled = False
    acceso.propagate = True
    acceso.addHandler(logging.NullHandler())
    app_de_prueba()
    assert acceso.disabled is True and acceso.propagate is False and acceso.handlers == []


def test_un_registro_del_access_log_no_sale_a_ninguna_salida(logs, capsys):
    app_de_prueba()
    logging.getLogger("uvicorn.access").info('%s - "GET /negocios/x?email=%s HTTP/1.1" 200', IP, EMAIL)
    assert EMAIL not in logs.getvalue() and IP not in logs.getvalue()
    assert EMAIL not in capsys.readouterr().err


def test_el_comando_documentado_de_arranque_lleva_no_access_log():
    readme = (PAQUETE.parent / "README.md").read_text(encoding="utf-8")
    comando = next(linea for linea in readme.splitlines() if linea.startswith("Arranque: `uvicorn"))
    assert "--no-access-log" in comando and "--factory" in comando
    assert "--no-access-log" in (PAQUETE / "main.py").read_text(encoding="utf-8")
