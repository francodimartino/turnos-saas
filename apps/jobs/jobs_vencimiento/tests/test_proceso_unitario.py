"""Job de vencimiento con una base simulada: aislamiento de fallos, avisos y reporte, sin Postgres."""

import json
import secrets
import uuid
from contextlib import asynccontextmanager

import httpx

from jobs_vencimiento import ejecutar

TOKEN = f"{secrets.randbelow(10**9)}:{secrets.token_urlsafe(24)}"
CHAT_PII = "5491155550001"


class ConexionFalsa:
    def __init__(self, base):
        self.base = base
        self.contexto = None

    async def execute(self, sql, *args):
        if "set_config('app.current_tenant_id'" in sql:
            self.contexto = args[0]
        return None

    async def fetch(self, sql, *args):
        assert "expire_pending_bookings" in sql
        return self.base.filas

    async def fetchval(self, sql, *args):
        if "prune_bot_sessions" in sql:
            if self.base.falla_prune:
                raise RuntimeError("prune roto")
            return self.base.borradas
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
