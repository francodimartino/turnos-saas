"""Monitor de cron de Sentry compartido: check-ins, configuración y errores."""

import pytest

from jobs_comun import ejecutar_con_monitor


class Checkins:
    def __init__(self):
        self.llamadas = []

    def __call__(self, **kwargs):
        self.llamadas.append(kwargs)
        return "check-in-1"


class _Resultado:
    def __init__(self, ok):
        self.ok = ok


PARAMETROS = {"monitor_slug": "un-job", "cron": "*/5 * * * *", "margen_minutos": 3, "duracion_maxima_minutos": 9}


async def test_check_in_de_inicio_con_la_configuracion_y_de_fin_ok():
    checkins = Checkins()

    async def operacion():
        return _Resultado(True)

    await ejecutar_con_monitor(operacion, checkin=checkins, reloj=iter([1.0, 3.5]).__next__, **PARAMETROS)

    inicio, fin = checkins.llamadas
    assert inicio["monitor_slug"] == "un-job" and inicio["status"] == "in_progress"
    assert inicio["monitor_config"] == {
        "schedule": {"type": "crontab", "value": "*/5 * * * *"},
        "timezone": "UTC",
        "checkin_margin": 3,
        "max_runtime": 9,
    }
    assert fin["status"] == "ok" and fin["check_in_id"] == "check-in-1" and fin["duration"] == 2.5


async def test_check_in_error_si_la_corrida_no_fue_limpia_o_lanza():
    checkins = Checkins()

    async def sucia():
        return _Resultado(False)

    await ejecutar_con_monitor(sucia, checkin=checkins, **PARAMETROS)
    assert checkins.llamadas[-1]["status"] == "error"

    checkins = Checkins()

    async def lanza():
        raise RuntimeError("caída")

    with pytest.raises(RuntimeError):
        await ejecutar_con_monitor(lanza, checkin=checkins, **PARAMETROS)
    assert [c["status"] for c in checkins.llamadas] == ["in_progress", "error"]
