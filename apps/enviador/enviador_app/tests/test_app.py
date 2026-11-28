"""App del enviador: `/health` mínimo, rol verificado al arrancar y apagado ordenado."""

import importlib
import sys
import time

import pytest
from fastapi.testclient import TestClient
from turnos_config import ConfigInvalida

from enviador_app.app import RolIncorrecto, crear_app
from enviador_app.config import Config
from enviador_app.main import crear_app_desde_entorno
from enviador_app.tests.soporte import EMAIL_CLIENTE, CorreoSimulado, Mundo, Reportes, Telegram

ENTORNO = {
    "ENVIADOR_DATABASE_URL": "postgresql://enviador:clave-de-prueba@h:5432/postgres",
    "RESEND_API_KEY": "re_clave_de_prueba",
    "EMAIL_REMITENTE": "avisos@agendemos.ar",
    "ENVIADOR_INTERVALO_SEG": "0.1",
    "ENVIADOR_ESPERA_MAXIMA_SEG": "0.2",
    "ENVIADOR_PLAZO_APAGADO_SEG": "1",
}


def _app(mundo=None, **cambios):
    mundo = mundo or Mundo()
    config = Config.desde_entorno({**ENTORNO, **cambios})
    return (
        crear_app(
            config,
            pool=mundo,
            cliente_http=Telegram().cliente,
            cliente_email=CorreoSimulado(),
            reportar=Reportes(),
        ),
        mundo,
    )


def test_health_responde_ok_sin_exponer_datos():
    app, _ = _app()
    with TestClient(app) as cliente:
        r = cliente.get("/health")
    assert (r.status_code, r.json()) == (200, {"estado": "ok"})
    assert r.headers["x-content-type-options"] == "nosniff"


def test_no_hay_documentacion_ni_otras_rutas():
    app, _ = _app()
    with TestClient(app) as cliente:
        assert [cliente.get(r).status_code for r in ("/docs", "/openapi.json", "/redoc", "/")] == [404] * 4


@pytest.mark.parametrize("cambio", ["rol", "base"])
def test_health_responde_503_sin_detalles_si_la_base_falla_o_el_rol_no_es_el_del_enviador(cambio):
    app, mundo = _app()
    with TestClient(app) as cliente:
        if cambio == "rol":
            mundo.rol = "postgres"
        else:
            mundo.falla_fetchrow = RuntimeError(f"no conecta {EMAIL_CLIENTE}")
        r = cliente.get("/health")
    assert (r.status_code, r.json()) == (503, {"estado": "no_disponible"})
    assert EMAIL_CLIENTE not in r.text


def test_health_responde_503_si_el_bucle_se_cayo():
    app, _ = _app()
    with TestClient(app) as cliente:
        app.state.tarea.get_loop().call_soon_threadsafe(app.state.detener.set)  # el bucle termina por su cuenta
        time.sleep(0.3)
        assert cliente.get("/health").status_code == 503


def test_health_responde_503_si_el_bucle_dejo_de_dar_vueltas():
    app, _ = _app()
    with TestClient(app) as cliente:
        app.state.enviador.ultimo_ciclo = time.monotonic() - 10**6
        assert cliente.get("/health").status_code == 503


def test_arrancar_con_otro_rol_falla_y_no_deja_el_bucle_corriendo():
    mundo = Mundo()
    mundo.rol = "postgres"
    app, _ = _app(mundo)
    with pytest.raises(RolIncorrecto), TestClient(app):
        pass
    assert mundo.reclamos == []  # nunca llegó a reclamar nada


def test_el_apagado_detiene_el_bucle_y_cierra_lo_que_abrio():
    app, mundo = _app()
    with TestClient(app):
        time.sleep(0.3)  # el bucle ya reclamó al menos una vez
        tarea = app.state.tarea
        assert not tarea.done() and mundo.reclamos
    assert tarea.done() and not tarea.cancelled()


def test_el_servicio_reclama_con_los_parametros_de_la_config():
    app, mundo = _app(None, OUTBOX_ARRIENDO_SEG="450", OUTBOX_MAX_INTENTOS="3", ENVIADOR_LOTE="7")
    with TestClient(app):
        time.sleep(0.3)
    assert set(mundo.reclamos) == {(7, 450, 3)}


def test_sin_configuracion_valida_no_se_puede_crear_la_app(monkeypatch):
    monkeypatch.delenv("ENVIADOR_DATABASE_URL", raising=False)
    with pytest.raises(ConfigInvalida, match="ENVIADOR_DATABASE_URL"):
        crear_app_desde_entorno()


def test_el_entrypoint_arma_la_app_desde_el_entorno(monkeypatch):
    for clave, valor in ENTORNO.items():
        monkeypatch.setenv(clave, valor)
    assert crear_app_desde_entorno().title == "turnos-enviador"


def test_importar_el_entrypoint_no_lee_el_entorno(monkeypatch):
    monkeypatch.delenv("ENVIADOR_DATABASE_URL", raising=False)
    sys.modules.pop("enviador_app.main", None)
    assert importlib.import_module("enviador_app.main").crear_app_desde_entorno
