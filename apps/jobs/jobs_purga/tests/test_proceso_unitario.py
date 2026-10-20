"""Orquestación del job de purga sin base: monitor de cron, código de salida, calendario sin configurar, logs."""

import logging
import uuid

import pytest
from platform_admin import ResultadoPurga, ResultadoTenantPurga

from jobs_purga import proceso
from jobs_purga.proceso import (
    MONITOR_SLUG,
    ResultadoJob,
    ejecutar_con_monitor,
    ejecutar_job,
    exit_code,
    fabrica_calendario_desde_entorno,
    log_paso_purga,
)
from jobs_purga.reintento_calendar import ResultadoLimpiezas

ENTORNO_GOOGLE = {
    "GOOGLE_OAUTH_CLIENT_ID": "id-de-prueba",
    "GOOGLE_OAUTH_CLIENT_SECRET": "valor-de-prueba",
    "GOOGLE_OAUTH_REDIRECT_URI": "https://ejemplo.test/callback",
}


class Checkins:
    def __init__(self):
        self.llamadas: list[dict] = []

    def __call__(self, **kwargs):
        self.llamadas.append(kwargs)
        return "check-in-1"


def _resultado(fallidos=0, error_poda=False):
    tenants = tuple(ResultadoTenantPurga(uuid.uuid4(), 100, "fallido", "delete_auth_accounts") for _ in range(fallidos))
    return ResultadoPurga(tenants + (ResultadoTenantPurga(uuid.uuid4(), 100, "purged"),), 0, error_poda=error_poda)


def test_exit_code_cero_solo_si_la_corrida_fue_limpia():
    assert exit_code(_resultado()) == 0
    assert exit_code(_resultado(fallidos=1)) == 1
    assert exit_code(_resultado(error_poda=True)) == 1


async def test_checkin_al_iniciar_y_al_terminar_ok():
    checkins = Checkins()

    async def operacion():
        return _resultado()

    resultado = await ejecutar_con_monitor(operacion, checkin=checkins, reloj=iter([10.0, 12.5]).__next__)

    assert resultado.ok
    inicio, fin = checkins.llamadas
    assert inicio["monitor_slug"] == MONITOR_SLUG and inicio["status"] == "in_progress"
    assert fin["monitor_slug"] == MONITOR_SLUG and fin["status"] == "ok"
    assert fin["check_in_id"] == "check-in-1" and fin["duration"] == 2.5


async def test_checkin_con_error_si_algun_tenant_fallo():
    checkins = Checkins()

    async def operacion():
        return _resultado(fallidos=1)

    await ejecutar_con_monitor(operacion, checkin=checkins)
    assert checkins.llamadas[-1]["status"] == "error"


async def test_checkin_con_error_si_la_operacion_lanza_y_la_excepcion_sigue_subiendo():
    checkins = Checkins()

    async def operacion():
        raise RuntimeError("caída")

    with pytest.raises(RuntimeError):
        await ejecutar_con_monitor(operacion, checkin=checkins)
    assert [c["status"] for c in checkins.llamadas] == ["in_progress", "error"]


async def test_sin_variables_de_google_el_paso_0_registra_el_fallo_de_configuracion_y_no_lanza(monkeypatch):
    for variable in ENTORNO_GOOGLE:
        monkeypatch.delenv(variable, raising=False)
    fabrica = fabrica_calendario_desde_entorno(http=None)
    integracion = uuid.uuid4()

    fallidos = await fabrica(object()).limpiar_eventos([(integracion, "evt-1"), (integracion, "evt-2")])

    assert [(f.integration_id, f.event_id, f.motivo) for f in fallidos] == [
        (integracion, "evt-1", "calendar_no_configurado"),
        (integracion, "evt-2", "calendar_no_configurado"),
    ]


def test_con_variables_de_google_arma_el_servicio_real(monkeypatch):
    conexiones = []
    monkeypatch.setattr(proceso, "crear_almacen", lambda conn: conexiones.append(conn) or "almacen")
    creados = []
    monkeypatch.setattr(
        proceso, "crear_calendario", lambda config, almacen, http: creados.append((almacen, http)) or "svc"
    )

    for variable, valor in ENTORNO_GOOGLE.items():
        monkeypatch.setenv(variable, valor)
    fabrica = fabrica_calendario_desde_entorno(http="cliente-http")

    assert fabrica("conexion") == "svc"
    assert conexiones == ["conexion"] and creados == [("almacen", "cliente-http")]


def test_job_arma_el_servicio_real_sin_calendar_oauth_state_secret(monkeypatch):
    # El job nunca inicia un OAuth: con client id/secret/redirect alcanza para limpiar eventos.
    monkeypatch.setattr(proceso, "crear_almacen", lambda conn: "almacen")
    configs = []
    monkeypatch.setattr(proceso, "crear_calendario", lambda config, almacen, http: configs.append(config) or "svc")
    for variable, valor in ENTORNO_GOOGLE.items():
        monkeypatch.setenv(variable, valor)
    monkeypatch.delenv("CALENDAR_OAUTH_STATE_SECRET", raising=False)

    fabrica = fabrica_calendario_desde_entorno(http="cliente-http")

    assert fabrica("conexion") == "svc"
    assert configs[0].state_secret is None


def test_log_paso_usa_el_evento_obligatorio_tenant_purge_step(caplog):
    with caplog.at_level(logging.INFO, logger="turnos_observabilidad.eventos"):
        log_paso_purga(
            "tenant_purge_step", tenant_id="t-1", actor_type="system", step="calendar_scrub", resultado="ok", count=2
        )
    registro = caplog.records[-1]
    assert registro.evento == "tenant_purge_step" and registro.step == "calendar_scrub" and registro.count == 2


def test_exit_code_del_job_es_1_si_las_limpiezas_de_calendar_persisten():
    assert exit_code(ResultadoJob(_resultado(), ResultadoLimpiezas(resueltas=2))) == 0
    assert exit_code(ResultadoJob(_resultado(), ResultadoLimpiezas(persistentes=1))) == 1
    assert exit_code(ResultadoJob(_resultado(), ResultadoLimpiezas(errores=1))) == 1
    assert exit_code(ResultadoJob(_resultado(fallidos=1), ResultadoLimpiezas())) == 1


async def test_el_monitor_de_cron_queda_en_error_si_las_limpiezas_persisten():
    checkins = Checkins()

    async def operacion():
        return ResultadoJob(_resultado(), ResultadoLimpiezas(persistentes=1))

    await ejecutar_con_monitor(operacion, checkin=checkins)
    assert checkins.llamadas[-1]["status"] == "error"


async def test_ejecutar_job_corre_la_purga_y_despues_los_reintentos(monkeypatch):
    orden = []

    async def purga(pool, auth, fabrica, reportador):
        orden.append("purga")
        return _resultado()

    async def reintentos(pool, fabrica, reportador, config):
        orden.append("reintentos")
        return ResultadoLimpiezas(resueltas=1)

    monkeypatch.setattr(proceso, "ejecutar", purga)

    async def ancla(pool, reportador):
        orden.append("ancla")
        return True

    monkeypatch.setattr(proceso, "reintentar_limpiezas", reintentos)
    monkeypatch.setattr(proceso, "anclar_cadenas", ancla)

    resultado = await ejecutar_job("pool", "auth", "fabrica", "reportador")

    assert orden == ["purga", "reintentos", "ancla"]
    assert resultado.ok and resultado.purgados == 1 and resultado.fallidos == 0


def test_exit_code_es_1_si_no_se_pudo_anclar_la_cadena_y_0_si_lo_unico_pendiente_es_sin_integracion():
    assert exit_code(ResultadoJob(_resultado(), ResultadoLimpiezas(), ancla_ok=False)) == 1
    solo_huerfanas = ResultadoLimpiezas(sin_integracion=3, sin_integracion_persistentes=3)
    assert exit_code(ResultadoJob(_resultado(), solo_huerfanas)) == 0


async def test_si_el_anclaje_falla_la_purga_y_los_reintentos_igual_se_completan(monkeypatch):
    orden = []

    async def purga(pool, auth, fabrica, reportador):
        orden.append("purga")
        return _resultado()

    async def reintentos(pool, fabrica, reportador, config):
        orden.append("reintentos")
        return ResultadoLimpiezas(resueltas=1)

    class PoolRoto:
        def acquire(self):
            raise RuntimeError("caída")

    class Rep:
        def __init__(self):
            self.n = 0

        def capturar_excepcion(self, exc, **ctx):
            self.n += 1

    monkeypatch.setattr(proceso, "ejecutar", purga)
    monkeypatch.setattr(proceso, "reintentar_limpiezas", reintentos)
    rep = Rep()

    resultado = await ejecutar_job(PoolRoto(), "auth", "fabrica", rep)

    assert orden == ["purga", "reintentos"] and rep.n == 1
    assert resultado.ancla_ok is False and exit_code(resultado) == 1


def test_config_de_google_del_job_decide_google_sin_configurar_al_arrancar(monkeypatch):
    for variable in ENTORNO_GOOGLE:
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.delenv("CALENDAR_OAUTH_STATE_SECRET", raising=False)
    assert proceso.config_google_desde_entorno() is None


def test_config_de_google_a_medias_impide_arrancar_el_job(monkeypatch):
    from platform_calendar import ErrorConfiguracionCalendar

    for variable in ENTORNO_GOOGLE:
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "id-de-prueba")
    with pytest.raises(ErrorConfiguracionCalendar, match="GOOGLE_OAUTH_CLIENT_SECRET"):
        proceso.config_google_desde_entorno()


def test_la_fabrica_con_la_config_inyectada_no_vuelve_a_leer_el_entorno(monkeypatch):
    from platform_calendar import ConfigGoogle

    monkeypatch.setattr(proceso, "crear_almacen", lambda conn: "almacen")
    creados = []
    monkeypatch.setattr(
        proceso, "crear_calendario", lambda config, almacen, http: creados.append((config, http)) or "svc"
    )
    for variable in ENTORNO_GOOGLE:
        monkeypatch.delenv(variable, raising=False)  # el entorno ya no importa: la config llega decidida
    config = ConfigGoogle("id", "secreto", "https://ejemplo.test/cb")

    fabrica = proceso.fabrica_calendario(config, "cliente-http")

    assert fabrica("conexion") == "svc" and fabrica("otra") == "svc"
    assert creados == [(config, "cliente-http")] * 2
