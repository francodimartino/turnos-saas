"""Reescaneo del outbox dentro de la corrida del job de vencimiento (§6.4), con base y Telegram simulados."""

import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
import pytest
from platform_tenancy.testing import TransaccionPorExecute
from turnos_negocio import PLANTILLA_VENCIMIENTO, mensaje_aviso_turno

from jobs_vencimiento import ejecutar

UTC = timezone.utc  # noqa: UP017
INICIO = datetime(2026, 10, 21, 15, 30, tzinfo=UTC)
CHAT = "5491155550077"
TOKEN = f"123456:{uuid.uuid4().hex}"
PLANTILLAS_DE_TURNO = {
    "turno_vencido",
    "turno_confirmado",
    "turno_rechazado",
    "turno_cancelado",
    "turno_movido",
    "reserva_confirmada",
    "reserva_pendiente",
}


class Mundo:
    """Estado de la base falsa: filas reclamables del outbox, lo que se cerró y las llamadas hechas."""

    def __init__(self, filas):
        self.filas = filas  # pendientes del outbox, reclamables o no
        self.destinos = {}  # id de la fila -> destinatarios que devuelve resolve_outbox_delivery
        self.al_resolver = {}  # id -> destinatarios distintos al resolver (cambiaron entre el reclamo y el envío)
        self.cerradas = []  # (id, status, error, retry, tenant del contexto)
        self.entregas = []  # (id, clave, estado, error, tenant del contexto)
        self.reclamadas = []  # ids que el reclamo entregó (y arrendó)
        self.filtra_plantillas = True
        self.reclamos = []
        self.podas = []
        self.integraciones = set()
        self.falla_reclamo = False

    def poner_destinos(self, id_, destinos):
        """`destinos`: una fila, una lista de filas o `None` (la fila o el turno ya no existen)."""
        self.destinos[id_] = [] if destinos is None else destinos if isinstance(destinos, list) else [destinos]

    def reclamable(self, fila):
        """Lo que `reclamar_avisos_telegram` entrega: destino cliente, plantilla de turno y Telegram sin entregar."""
        if fila.get("destino", "cliente") != "cliente":
            return False
        if self.filtra_plantillas and fila["template"] not in PLANTILLAS_DE_TURNO:
            return False
        return any(d["channel"] == "telegram" and not d["entregado"] for d in self.destinos.get(fila["id"], []))

    @asynccontextmanager
    async def acquire(self):
        yield Conexion(self)


class Conexion(TransaccionPorExecute):
    def __init__(self, mundo):
        self.m = mundo
        self.contexto = None

    async def execute(self, sql, *args):
        if "set_config('app.current_tenant_id'" in sql:
            self.contexto = args[0]

    async def fetch(self, sql, *args):
        if "claim_notification_outbox" in sql:
            raise AssertionError("el reescaneo no debe usar el reclamo de todos los canales")
        if "reclamar_avisos_telegram" in sql:
            if self.m.falla_reclamo:
                raise RuntimeError("claim roto")
            self.m.reclamos.append(args)
            # mismo criterio que la función de la base: cliente, plantilla de turno y Telegram sin entregar
            propias = [f for f in self.m.filas if self.m.reclamable(f)]
            self.m.filas = [f for f in self.m.filas if f not in propias]
            self.m.reclamadas += [f["id"] for f in propias]
            return propias
        if "resolve_outbox_delivery" in sql:
            return self.m.al_resolver.get(args[0], self.m.destinos.get(args[0], []))
        assert "expire_pending_bookings" in sql, sql
        return []

    async def fetchval(self, sql, *args):
        if "prune_bot_sessions" in sql:
            return 0
        if "prune_notification_outbox" in sql:
            self.m.podas.append(args)
            return 4
        if "registrar_entrega_outbox" in sql:
            self.m.entregas.append((*args, self.contexto))
            return True
        if "complete_notification_outbox" in sql:
            self.m.cerradas.append((*args, self.contexto))
            return True
        if "find_integration" in sql:
            return uuid.uuid4() if self.contexto in self.m.integraciones else None
        if "get_integration_secret" in sql:
            return json.dumps({"bot_token": TOKEN})
        if "record_notification_attempt" in sql:
            return uuid.uuid4()
        raise AssertionError(sql)


def _fila(tenant, template=PLANTILLA_VENCIMIENTO, intentos=1):
    return {
        "id": uuid.uuid4(),
        "tenant_id": tenant,
        "end_customer_id": uuid.uuid4(),
        "channel": "telegram",
        "template": template,
        "params": json.dumps({"booking_id": str(uuid.uuid4()), "inicio": INICIO.isoformat()}),
        "attempts": intentos,
    }


def _destino(**cambios):
    base = {
        "channel": "telegram",
        "channel_user_id": CHAT,
        "tenant_active": True,
        "booking_status": "cancelled",
        "service_name": "Corte de pelo",
        "professional_name": "Ana",
        "starts_at": INICIO,
        "pending_expires_at": None,
        "destino": "cliente",
        "clave": "cc:telegram",
        "entregado": False,
    }
    return {**base, **cambios}


def _canal(canal, identificador, *, entregado=False, destino="cliente", clave=None):
    """Una fila de `resolve_outbox_delivery` de otro canal (email, calendar...)."""
    return _destino(
        channel=canal,
        channel_user_id=identificador,
        entregado=entregado,
        destino=destino,
        clave=clave or f"cc:{canal}",
    )


def _mundo(*pares, integrar=True):
    """`pares`: (fila, destino). Deja a cada tenant con bot configurado salvo `integrar=False`."""
    m = Mundo([f for f, _ in pares])
    for f, d in pares:
        m.poner_destinos(f["id"], d)
        if integrar:
            m.integraciones.add(str(f["tenant_id"]))
    return m


async def _sin_espera(_):
    return None


def _http(enviados, estado=200):
    def handler(req):
        cuerpo = json.loads(req.content)
        if estado != 200:
            return httpx.Response(estado, json={"ok": False, "error_code": estado, "description": "x"})
        enviados.append(cuerpo)
        return httpx.Response(200, json={"ok": True})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class Reportes:
    def __init__(self):
        self.items = []

    def __call__(self, descripcion, **etiquetas):
        self.items.append((descripcion, etiquetas))


async def _correr(m, http, **kw):
    return await ejecutar(m, cliente_http=http, esperar=_sin_espera, reportar=kw.pop("reportar", Reportes()), **kw)


async def test_una_fila_abandonada_se_envia_con_el_texto_del_turno_y_queda_sent():
    t = uuid.uuid4()
    f = _fila(t)
    m = _mundo((f, _destino()))
    enviados = []
    r = await _correr(m, _http(enviados))
    assert [e["chat_id"] for e in enviados] == [CHAT]
    assert enviados[0]["text"] == mensaje_aviso_turno(
        PLANTILLA_VENCIMIENTO, servicio="Corte de pelo", profesional="Ana", inicio=INICIO
    )
    assert [(c[0], c[1], c[4]) for c in m.cerradas] == [(f["id"], "sent", str(t))]  # con el contexto de su tenant
    assert m.entregas == [(f["id"], "cc:telegram", "sent", None, str(t))]  # y registra el resultado del canal
    assert (r.reescaneadas, r.reescaneo_enviadas, r.fallos, r.exit_code) == (1, 1, 0, 0)


async def test_el_reclamo_usa_los_parametros_configurados_y_se_poda_el_outbox():
    m = _mundo()
    r = await _correr(m, _http([]), lote_outbox=7, arriendo_outbox=1200, max_intentos_outbox=3, retencion_outbox_dias=9)
    assert m.reclamos == [(7, 1200, 3)]
    assert m.podas == [(9,)]
    assert r.outbox_podadas == 4


async def test_el_texto_sale_del_turno_y_no_del_param_con_el_destinatario():
    t = uuid.uuid4()
    f = _fila(t, template="reserva_pendiente")
    m = _mundo((f, _destino(booking_status="pending", pending_expires_at=INICIO)))
    enviados = []
    await _correr(m, _http(enviados))
    assert "Corte de pelo con Ana" in enviados[0]["text"] and "pendiente de aprobación" in enviados[0]["text"]


async def test_error_transitorio_con_intentos_disponibles_se_reprograma_y_no_es_fallo():
    f = _fila(uuid.uuid4(), intentos=2)
    m = _mundo((f, _destino()))
    r = await _correr(m, _http([], estado=500), arriendo_outbox=600, max_intentos_outbox=5)
    assert [(c[1], c[3]) for c in m.cerradas] == [("pending", 600)]
    assert (r.reescaneo_reprogramadas, r.fallos, r.exit_code) == (1, 0, 0)


async def test_al_agotar_los_intentos_queda_failed_y_se_reporta():
    f = _fila(uuid.uuid4(), intentos=5)
    m = _mundo((f, _destino()))
    reportes = Reportes()
    r = await _correr(m, _http([], estado=500), max_intentos_outbox=5, reportar=reportes)
    assert [c[1] for c in m.cerradas] == ["failed"]
    assert (r.fallos, r.exit_code) == (1, 1) and len(reportes.items) == 1


async def test_error_permanente_del_proveedor_queda_failed_aunque_queden_intentos():
    f = _fila(uuid.uuid4(), intentos=1)
    m = _mundo((f, _destino()))
    r = await _correr(m, _http([], estado=403), max_intentos_outbox=5)
    assert [c[1] for c in m.cerradas] == ["failed"]
    assert r.fallos == 1


async def test_sin_integracion_del_tenant_queda_failed():
    f = _fila(uuid.uuid4())
    m = _mundo((f, _destino()), integrar=False)
    enviados = []
    r = await _correr(m, _http(enviados))
    assert enviados == [] and [(c[1], c[2]) for c in m.cerradas] == [("failed", "sin_integracion")]
    assert r.fallos == 1


@pytest.mark.parametrize(
    ("destino", "motivo"),
    [
        (None, "turno_inexistente"),
        (_destino(tenant_active=False), "tenant_inactivo"),
    ],
)
async def test_filas_sin_a_quien_avisar_se_descartan_sin_enviar_ni_contar_fallo(destino, motivo):
    f = _fila(uuid.uuid4())
    m = _mundo((f, _destino()))
    m.al_resolver[f["id"]] = [] if destino is None else [destino]
    enviados = []
    r = await _correr(m, _http(enviados))
    assert enviados == [] and [(c[1], c[2]) for c in m.cerradas] == [("failed", motivo)]
    assert (r.reescaneo_descartadas, r.fallos) == (1, 0)


async def test_plantilla_desconocida_se_descarta():
    f = _fila(uuid.uuid4(), template="otra_cosa")
    m = _mundo((f, _destino()))
    m.filtra_plantillas = False  # la base no la reclamaría; si llegara, el job no la envía
    r = await _correr(m, _http([]))
    assert [(c[1], c[2]) for c in m.cerradas] == [("failed", "plantilla_desconocida")]
    assert r.reescaneo_descartadas == 1


async def test_si_el_reclamo_falla_se_reporta_y_el_job_sigue_con_la_poda():
    m = _mundo()
    m.falla_reclamo = True
    reportes = Reportes()
    r = await _correr(m, _http([]), reportar=reportes)
    assert r.fallos == 1 and r.exit_code == 1 and m.podas
    assert "reescanear" in reportes.items[0][0]


async def test_no_se_filtran_destinatario_ni_token_a_los_reportes():
    f = _fila(uuid.uuid4(), intentos=5)
    m = _mundo((f, _destino()))
    reportes = Reportes()
    await _correr(m, _http([], estado=500), max_intentos_outbox=5, reportar=reportes)
    volcado = json.dumps([(d, {k: str(v) for k, v in e.items()}) for d, e in reportes.items])
    assert CHAT not in volcado and TOKEN not in volcado


def test_los_parametros_del_outbox_fuera_de_rango_o_invalidos_vuelven_al_valor_por_defecto(monkeypatch):
    from jobs_vencimiento.__main__ import _entero

    monkeypatch.delenv("OUTBOX_LOTE", raising=False)
    assert _entero("OUTBOX_LOTE", 50, 1, 500) == 50
    monkeypatch.setenv("OUTBOX_LOTE", "120")
    assert _entero("OUTBOX_LOTE", 50, 1, 500) == 120
    for malo in ("0", "501", "abc", ""):
        monkeypatch.setenv("OUTBOX_LOTE", malo)
        assert _entero("OUTBOX_LOTE", 50, 1, 500) == 50


# --- Convivencia con el servicio que envía por todos los canales ----------------------------------------------------


def _con_destino(fila, destino):
    return {**fila, "destino": destino}


async def test_solo_se_reclaman_avisos_del_cliente_con_telegram_sin_entregar_y_el_resto_queda_intacto():
    t = uuid.uuid4()
    negocio = _con_destino(_fila(t, template="aviso_negocio_reserva"), "negocio")
    calendar = _con_destino(_fila(t, template="sincronizacion_calendar"), "negocio")
    solo_email = _fila(t)
    telegram_entregado = _fila(t)
    mundo = _mundo(
        (negocio, _canal("email", "dueno@negocio.test", destino="negocio", clave="tu:1:email")),
        (calendar, _canal("calendar", None, destino="negocio", clave="calendar")),
        (solo_email, _canal("email", "cliente@correo.test")),
        (telegram_entregado, [_destino(entregado=True), _canal("email", "cliente@correo.test")]),
    )
    enviados = []
    r = await _correr(mundo, _http(enviados))
    assert mundo.reclamadas == [] and mundo.filas == [negocio, calendar, solo_email, telegram_entregado]
    assert enviados == [] and mundo.cerradas == [] and mundo.entregas == []  # ni envía, ni cierra, ni registra
    assert (r.reescaneadas, r.reescaneo_descartadas, r.fallos, r.exit_code) == (0, 0, 0, 0)


async def test_con_telegram_y_email_pendientes_envia_por_telegram_registra_y_no_cierra_la_fila():
    t = uuid.uuid4()
    f = _fila(t)
    m = _mundo((f, [_destino(), _canal("email", "cliente@correo.test")]))
    enviados = []
    r = await _correr(m, _http(enviados))
    assert [e["chat_id"] for e in enviados] == [CHAT]
    assert m.entregas == [(f["id"], "cc:telegram", "sent", None, str(t))]
    assert m.cerradas == []  # queda pending para el servicio que envía el email
    assert (r.reescaneo_enviadas, r.fallos) == (1, 0)


async def test_con_el_otro_canal_ya_entregado_registra_telegram_y_cierra_sent():
    f = _fila(uuid.uuid4())
    m = _mundo((f, [_destino(), _canal("email", "cliente@correo.test", entregado=True)]))
    await _correr(m, _http([]))
    assert [(e[1], e[2]) for e in m.entregas] == [("cc:telegram", "sent")]
    assert [c[1] for c in m.cerradas] == ["sent"]


async def test_si_telegram_falla_para_siempre_con_email_pendiente_registra_el_fallo_y_no_cierra():
    f = _fila(uuid.uuid4(), intentos=1)
    m = _mundo((f, [_destino(), _canal("email", "cliente@correo.test")]))
    r = await _correr(m, _http([], estado=403), max_intentos_outbox=5)
    assert [(e[1], e[2]) for e in m.entregas] == [("cc:telegram", "failed")]
    assert m.cerradas == [] and r.fallos == 1


async def test_si_telegram_ya_se_entrego_cuando_se_resuelve_no_se_vuelve_a_enviar_ni_se_cierra():
    f = _fila(uuid.uuid4())
    m = _mundo((f, _destino()))
    m.al_resolver[f["id"]] = [_destino(entregado=True), _canal("email", "cliente@correo.test")]  # lo entregó otro
    enviados = []
    await _correr(m, _http(enviados))
    assert enviados == [] and m.cerradas == [] and m.entregas == []


async def test_si_el_cliente_perdio_telegram_entre_el_reclamo_y_el_envio_la_fila_no_se_cierra():
    f = _fila(uuid.uuid4())
    m = _mundo((f, _destino()))
    m.al_resolver[f["id"]] = [_canal("email", "cliente@correo.test")]
    enviados = []
    r = await _correr(m, _http(enviados))
    assert enviados == [] and m.cerradas == [] and m.entregas == []
    assert (r.reescaneo_descartadas, r.fallos) == (0, 0)


async def test_sin_integracion_con_email_pendiente_registra_el_fallo_de_telegram_y_no_cierra():
    f = _fila(uuid.uuid4())
    m = _mundo((f, [_destino(), _canal("email", "cliente@correo.test")]), integrar=False)
    await _correr(m, _http([]))
    assert [(e[1], e[2], e[3]) for e in m.entregas] == [("cc:telegram", "failed", "sin_integracion")]
    assert m.cerradas == []
