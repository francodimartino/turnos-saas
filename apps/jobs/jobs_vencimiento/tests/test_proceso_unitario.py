"""Job de vencimiento con una base simulada: aislamiento de fallos, avisos y reporte, sin Postgres."""

import json
import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
import pytest
from turnos_negocio import mensaje_vencimiento

from jobs_vencimiento import Resultado, ejecutar

TOKEN = f"{secrets.randbelow(10**9)}:{secrets.token_urlsafe(24)}"
CHAT_PII = "5491155550001"
INICIO = datetime(2026, 10, 21, 15, 30, tzinfo=timezone.utc)  # noqa: UP017  # miércoles 21/10 12:30 en Buenos Aires
SERVICIO = "Corte de pelo"


class ConexionFalsa:
    def __init__(self, base):
        self.base = base
        self.contexto = None

    async def execute(self, sql, *args):
        if "set_config('app.current_tenant_id'" in sql:
            self.contexto = args[0]
        return

    async def fetch(self, sql, *args):
        if "claim_notification_outbox" in sql:
            return []  # el reescaneo del outbox tiene su propia suite (test_reescaneo_unitario.py)
        assert "expire_pending_bookings" in sql
        return self.base.filas

    async def fetchval(self, sql, *args):
        if "prune_bot_sessions" in sql:
            if self.base.falla_prune:
                raise RuntimeError("prune roto")
            return self.base.borradas
        if "prune_notification_outbox" in sql:
            return 0
        if "complete_notification_outbox" in sql:
            return True
        if "find_integration" in sql:
            return self.base.integraciones.get(self.contexto)
        if "get_integration_secret" in sql:
            return json.dumps({"bot_token": TOKEN})
        if "record_notification_attempt" in sql:
            self.base.registros.append(args)
            return uuid.uuid4()
        raise AssertionError(sql)


class BaseFalsa:
    def __init__(self, filas):
        self.filas = filas
        self.integraciones = {}
        self.registros = []
        self.borradas = 3
        self.falla_prune = False

    @asynccontextmanager
    async def acquire(self):
        yield ConexionFalsa(self)


def fila(tenant, notify=True, con_cliente=False, chat=CHAT_PII):
    datos = {
        "tenant_id": tenant,
        "booking_id": uuid.uuid4(),
        "channel": "telegram" if notify else None,
        "channel_user_id": chat if notify else None,
        "notify": notify,
        "starts_at": INICIO,
        "service_name": SERVICIO,
    }
    if con_cliente:
        datos["end_customer_id"] = uuid.uuid4()
    return datos


async def _sin_espera(_):
    return None


def _http(enviados, falla_para=None):
    def handler(req):
        cuerpo = json.loads(req.content)
        if falla_para and cuerpo["chat_id"] == falla_para:
            return httpx.Response(400, json={"ok": False, "error_code": 400, "description": "chat not found"})
        enviados.append(cuerpo)
        return httpx.Response(200, json={"ok": True})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class Reportes:
    def __init__(self):
        self.items = []

    def __call__(self, descripcion, **etiquetas):
        self.items.append((descripcion, etiquetas))


async def test_avisa_a_quienes_corresponde_y_poda_sesiones():
    t1, t2 = uuid.uuid4(), uuid.uuid4()
    base = BaseFalsa([fila(t1), fila(t2, notify=False)])
    base.integraciones = {str(t1): uuid.uuid4()}
    enviados, reportes = [], Reportes()
    r = await ejecutar(base, cliente_http=_http(enviados), esperar=_sin_espera, reportar=reportes)
    assert (r.vencidos, r.avisados, r.sin_aviso, r.fallos, r.sesiones_borradas) == (2, 1, 1, 0, 3)
    assert [e["chat_id"] for e in enviados] == [CHAT_PII]
    assert enviados[0]["text"] == mensaje_vencimiento(INICIO, SERVICIO)
    assert "Corte de pelo" in enviados[0]["text"] and "miércoles 21/10 a las 12:30" in enviados[0]["text"]
    assert reportes.items == []
    assert r.exit_code == 0


async def test_un_fallo_no_corta_el_lote_y_se_reporta_sin_pii():
    t1, t2, t3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    malo = "999000111"
    base = BaseFalsa([fila(t1, chat=malo), fila(t2), fila(t3)])
    base.integraciones = {str(t1): uuid.uuid4(), str(t3): uuid.uuid4()}  # t2 sin integración
    enviados, reportes = [], Reportes()
    r = await ejecutar(base, cliente_http=_http(enviados, falla_para=malo), esperar=_sin_espera, reportar=reportes)
    assert (r.avisados, r.fallos, r.sesiones_borradas) == (1, 2, 3)
    assert r.exit_code == 1
    assert len(reportes.items) == 2
    volcado = json.dumps([(d, {k: str(v) for k, v in e.items()}) for d, e in reportes.items])
    assert CHAT_PII not in volcado and malo not in volcado and TOKEN not in volcado


async def test_con_end_customer_id_registra_en_notifications_log():
    t1 = uuid.uuid4()
    f = fila(t1, con_cliente=True)
    base = BaseFalsa([f])
    base.integraciones = {str(t1): uuid.uuid4()}
    await ejecutar(base, cliente_http=_http([]), esperar=_sin_espera, reportar=Reportes())
    estados = [a[3] for a in base.registros]  # record_notification_attempt($1..$7): status es $4
    assert estados == ["pending", "sent"]
    assert all(a[0] == f["end_customer_id"] for a in base.registros)


async def test_si_falla_prune_igual_procesa_avisos_y_sale_con_error():
    t1 = uuid.uuid4()
    base = BaseFalsa([fila(t1)])
    base.integraciones = {str(t1): uuid.uuid4()}
    base.falla_prune = True
    enviados, reportes = [], Reportes()
    r = await ejecutar(base, cliente_http=_http(enviados), esperar=_sin_espera, reportar=reportes)
    assert r.avisados == 1 and r.exit_code == 1 and len(reportes.items) == 1


async def test_sin_vencidos_no_hace_nada():
    r = await ejecutar(BaseFalsa([]), cliente_http=_http([]), esperar=_sin_espera, reportar=Reportes())
    assert (r.vencidos, r.avisados, r.fallos, r.exit_code) == (0, 0, 0, 0)


def test_sin_select_directo_a_tablas():
    import pathlib

    import jobs_vencimiento

    raiz = pathlib.Path(jobs_vencimiento.__file__).parent
    fuente = "\n".join(p.read_text(encoding="utf-8") for p in raiz.glob("*.py"))
    import re

    # `from public.<función>(` es una llamada a función; sin paréntesis sería una tabla.
    assert not re.search(r"(?i)\bfrom\s+public\.\w+\b(?!\()", fuente)
    assert not re.search(r"(?i)\b(insert\s+into|update\s+public|delete\s+from)\b", fuente)


def test_main_sin_configuracion_sale_con_error(monkeypatch):
    from jobs_vencimiento.__main__ import main

    monkeypatch.delenv("SCHEDULER_DATABASE_URL", raising=False)
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert main() == 1


# --- Corte por credencial caída y envío paralelo acotado ---


def _http_lento(demora, estado=200, activos=None):
    """Telegram simulado que tarda `demora` s por envío; `activos` registra la concurrencia máxima y los intentos."""
    import asyncio

    async def handler(req):
        activos["intentos"] += 1
        activos["ahora"] += 1
        activos["max"] = max(activos["max"], activos["ahora"])
        await asyncio.sleep(demora)
        activos["ahora"] -= 1
        if estado == 200:
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(estado, json={"ok": False, "error_code": estado, "description": "Unauthorized"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _activos():
    return {"intentos": 0, "ahora": 0, "max": 0}


async def test_token_revocado_corta_al_tenant_con_un_unico_intento_y_un_unico_fallo():
    t1 = uuid.uuid4()
    filas = [fila(t1, con_cliente=True, chat=f"{CHAT_PII}{i}") for i in range(30)]
    base = BaseFalsa(filas)
    base.integraciones = {str(t1): uuid.uuid4()}
    activos, reportes = _activos(), Reportes()
    r = await ejecutar(base, cliente_http=_http_lento(0, 401, activos), esperar=_sin_espera, reportar=reportes)
    assert activos["intentos"] == 1
    assert (r.vencidos, r.avisados, r.fallos, r.omitidos) == (30, 0, 1, 29)
    assert r.exit_code == 1
    assert len(reportes.items) == 1
    # Los 30 avisos quedan registrados como failed (el de la sonda recorre pending/failed por el envío).
    assert sum(1 for a in base.registros if a[3] == "failed") == 30
    volcado = json.dumps([(d, {k: str(v) for k, v in e.items()}) for d, e in reportes.items])
    assert TOKEN not in volcado and CHAT_PII not in volcado
    assert all(TOKEN not in str(a) for a in base.registros)


async def test_credencial_inexistente_corta_sin_intentar_ningun_envio():
    t1 = uuid.uuid4()
    base = BaseFalsa([fila(t1, con_cliente=True) for _ in range(5)])  # sin integración
    activos = _activos()
    r = await ejecutar(base, cliente_http=_http_lento(0, 200, activos), esperar=_sin_espera, reportar=Reportes())
    assert activos["intentos"] == 0
    assert (r.fallos, r.omitidos) == (1, 4)


async def test_un_tenant_revocado_no_afecta_a_otro():
    malo, bueno = uuid.uuid4(), uuid.uuid4()
    base = BaseFalsa([fila(malo, chat="777") for _ in range(3)] + [fila(bueno) for _ in range(3)])
    base.integraciones = {str(malo): uuid.uuid4(), str(bueno): uuid.uuid4()}

    def handler(req):
        if json.loads(req.content)["chat_id"] == "777":  # el chat marca al tenant revocado
            return httpx.Response(403, json={"ok": False, "error_code": 403, "description": "Forbidden"})
        return httpx.Response(200, json={"ok": True})

    r = await ejecutar(
        base,
        cliente_http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        esperar=_sin_espera,
        reportar=Reportes(),
    )
    assert (r.avisados, r.fallos, r.omitidos) == (3, 1, 2)


async def test_30_envios_lentos_respetan_la_concurrencia_y_terminan_acotados():
    import time

    tenants = [uuid.uuid4() for _ in range(6)]
    base = BaseFalsa([fila(t) for t in tenants for _ in range(5)])
    base.integraciones = {str(t): uuid.uuid4() for t in tenants}
    activos = _activos()
    inicio = time.monotonic()
    r = await ejecutar(
        base, cliente_http=_http_lento(0.05, 200, activos), esperar=_sin_espera, reportar=Reportes(), concurrencia=5
    )
    duracion = time.monotonic() - inicio
    assert r.avisados == 30 and r.exit_code == 0
    assert activos["max"] == 5
    assert duracion < 30 * 0.05 / 2  # muy por debajo del envío secuencial (1,5 s)


async def test_el_plazo_total_acota_la_corrida_y_cuenta_lo_pendiente_como_fallo():
    import time

    t1 = uuid.uuid4()
    base = BaseFalsa([fila(t1) for _ in range(30)])
    base.integraciones = {str(t1): uuid.uuid4()}
    reportes = Reportes()
    inicio = time.monotonic()
    r = await ejecutar(
        base,
        cliente_http=_http_lento(5, 200, _activos()),
        esperar=_sin_espera,
        reportar=reportes,
        concurrencia=5,
        plazo_total=0.2,
    )
    assert time.monotonic() - inicio < 2
    assert r.avisados == 0 and r.fallos == 30 and r.sesiones_borradas == 3
    assert len(reportes.items) == 1


class Checkins:
    def __init__(self):
        self.llamadas: list[dict] = []

    def __call__(self, **kwargs):
        self.llamadas.append(kwargs)
        return "check-in-1"


async def test_el_monitor_de_cron_usa_el_slug_y_el_horario_de_vencimiento_y_queda_ok():
    from jobs_vencimiento.proceso import CRON_POR_DEFECTO, MONITOR_SLUG, ejecutar_con_monitor_vencimiento

    checkins = Checkins()

    async def operacion():
        return Resultado()

    resultado = await ejecutar_con_monitor_vencimiento(operacion, checkin=checkins)

    assert resultado.exit_code == 0
    assert CRON_POR_DEFECTO == "*/15 * * * *"
    inicio, fin = checkins.llamadas
    assert inicio["monitor_slug"] == MONITOR_SLUG == "vencimiento-turnos"
    assert inicio["status"] == "in_progress"
    assert inicio["monitor_config"]["schedule"] == {"type": "crontab", "value": "*/15 * * * *"}
    assert fin["monitor_slug"] == MONITOR_SLUG and fin["status"] == "ok" and fin["check_in_id"] == "check-in-1"


async def test_el_monitor_de_cron_queda_en_error_si_hubo_fallos_o_la_corrida_lanza():
    from jobs_vencimiento.proceso import ejecutar_con_monitor_vencimiento

    checkins = Checkins()

    async def con_fallos():
        return Resultado(fallos=1)

    await ejecutar_con_monitor_vencimiento(con_fallos, checkin=checkins)
    assert checkins.llamadas[-1]["status"] == "error"

    checkins = Checkins()

    async def lanza():
        raise RuntimeError("caida")

    with pytest.raises(RuntimeError):
        await ejecutar_con_monitor_vencimiento(lanza, checkin=checkins)
    assert checkins.llamadas[-1]["status"] == "error"


async def test_el_horario_del_monitor_se_puede_sobreescribir():
    from jobs_vencimiento.proceso import ejecutar_con_monitor_vencimiento

    checkins = Checkins()

    async def operacion():
        return Resultado()

    await ejecutar_con_monitor_vencimiento(operacion, checkin=checkins, cron="*/30 * * * *")
    assert checkins.llamadas[0]["monitor_config"]["schedule"]["value"] == "*/30 * * * *"


def test_main_usa_vencimiento_cron_y_sin_variable_el_horario_por_defecto(monkeypatch):
    from jobs_vencimiento.__main__ import _cron

    monkeypatch.delenv("VENCIMIENTO_CRON", raising=False)
    assert _cron() == "*/15 * * * *"
    monkeypatch.setenv("VENCIMIENTO_CRON", "*/10 * * * *")
    assert _cron() == "*/10 * * * *"
    monkeypatch.setenv("VENCIMIENTO_CRON", "  ")
    assert _cron() == "*/15 * * * *"
