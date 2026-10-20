"""Calendar no retiene conexiones del pool mientras espera a Google (sincronizar turnos y limpiar clientes).

Se usa un pool de UNA conexión y un Google simulado lento: durante la llamada el servicio toma otra conexión del
mismo pool (si la request siguiera reteniendo la suya, se bloquearía) y lee la base a través de la conexión que le
entrega la fábrica. Datos ficticios; ningún test sale a la red.
"""

import asyncio
import json
import uuid
from contextlib import asynccontextmanager

import pytest
from platform_calendar import AccionSync, ErrorApiCalendar, ResultadoSync

from dashboard_backend import turnos as t
from dashboard_backend.tests.soporte import ORIGEN, http, token
from dashboard_backend.tests.soporte_clientes import ConnClientes, app_clientes, evento_no_limpiado
from dashboard_backend.tests.soporte_turnos import ConnTurnos, app_con_pool, fila_turno

TENANT = uuid.uuid4()
STAFF = uuid.uuid4()
ESPERA_MAX_SEG = 0.5


class PoolUno:
    """Pool de tamaño 1: una segunda adquisición simultánea espera; si pasa `ESPERA_MAX_SEG`, queda registrada."""

    def __init__(self, conn):
        self.conn = conn
        self._libre = asyncio.Semaphore(1)
        self.en_uso = 0
        self.bloqueos = 0

    @asynccontextmanager
    async def acquire(self):
        try:
            await asyncio.wait_for(self._libre.acquire(), ESPERA_MAX_SEG)
        except TimeoutError:
            self.bloqueos += 1  # no se lanza AssertionError: la ruta lo tragaría como "fallo de Calendar"
            raise
        self.en_uso += 1
        try:
            yield self.conn
        finally:
            self.en_uso -= 1
            self._libre.release()


class GoogleLento:
    """Servicio que tarda y, mientras tarda, usa el pool: directo y por la conexión que recibió de la fábrica."""

    def __init__(self, pool: PoolUno, conn_fabrica, resultado=None, error=None, fallidos=()):
        self.pool = pool
        self.conn_fabrica = conn_fabrica
        self.resultado = resultado
        self.error = error
        self.fallidos = list(fallidos)
        self.en_uso_durante_la_llamada: list[int] = []
        self.llamadas = 0

    async def _llamar(self):
        self.llamadas += 1
        self.en_uso_durante_la_llamada.append(self.pool.en_uso)
        await asyncio.sleep(0.05)  # Google tarda
        async with self.pool.acquire():  # otro uso concurrente del pool durante la espera
            pass
        await self.conn_fabrica.fetchval("select 1")  # lectura del refresh token, en su propia transacción corta
        if self.error is not None:
            raise self.error

    async def sincronizar_evento(self, profesional_id, evento):
        await self._llamar()
        return self.resultado or ResultadoSync(AccionSync.CREADO, "evt-nuevo")

    async def retirar_evento(self, profesional_id, id_externo):
        await self._llamar()
        return self.resultado or ResultadoSync(AccionSync.BORRADO, None)

    async def anonimizar_eventos(self, pares, texto):
        await self._llamar()
        return self.fallidos


def _fabrica(pool: PoolUno, **kw):
    servicios: list[GoogleLento] = []

    @asynccontextmanager
    async def _f(conn):
        servicio = GoogleLento(pool, conn, **kw)
        servicios.append(servicio)
        yield servicio

    _f.servicios = servicios
    return _f


def _h():
    return {"Cookie": "dash_acceso=" + token(user_id=STAFF, tenant_id=TENANT), "Origin": ORIGEN}


def _app_turnos(**kw):
    conn = ConnTurnos()
    pool = PoolUno(conn)
    fabrica = _fabrica(pool, **kw)
    app, _, _ = app_con_pool(pool, fabrica=fabrica)
    return app, conn, pool, fabrica


@pytest.mark.parametrize(
    ("accion", "cuerpo", "estado_inicial", "evento_inicial"),
    [
        ("aprobar", None, "pending", None),
        ("mover", {"start_at": "2030-03-05T13:00:00+00:00"}, "confirmed", "evt-1"),
        ("cancelar", None, "confirmed", "evt-1"),
    ],
)
async def test_el_turno_se_sincroniza_con_google_sin_retener_la_conexion(
    accion, cuerpo, estado_inicial, evento_inicial
):
    app, conn, pool, fabrica = _app_turnos()
    fila = fila_turno(TENANT, status=estado_inicial, google_calendar_event_id=evento_inicial)
    if estado_inicial == "confirmed":
        fila["pending_expires_at"] = None
    conn.turnos[fila["id"]] = fila
    r = await http(app).post(f"/turnos/{fila['id']}/{accion}", json=cuerpo, headers=_h())
    assert r.status_code == 200, r.text
    assert pool.bloqueos == 0
    (servicio,) = fabrica.servicios
    assert servicio.llamadas == 1
    assert servicio.en_uso_durante_la_llamada == [0]  # ninguna conexión tomada mientras se llamaba a Google
    assert r.json()["calendario"]["estado"] != "error"


async def test_el_id_del_evento_se_guarda_en_una_transaccion_corta_posterior():
    app, conn, pool, _ = _app_turnos()
    fila = fila_turno(TENANT)
    conn.turnos[fila["id"]] = fila
    r = await http(app).post(f"/turnos/{fila['id']}/aprobar", headers=_h())
    assert r.status_code == 200
    assert r.json()["calendario"] == {"estado": "creado"}
    assert conn.turnos[fila["id"]]["google_calendar_event_id"] == "evt-nuevo"
    sentencias = conn.sentencias()
    i_guardar = sentencias.index(t.SQL_GUARDAR_EVENTO)
    assert sentencias[i_guardar + 1] == "COMMIT"
    # Al guardar, la transacción abierta es la propia del guardado: todas las anteriores ya cerraron.
    assert sentencias[:i_guardar].count("BEGIN") == sentencias[:i_guardar].count("COMMIT") + 1


async def test_si_google_falla_el_turno_queda_confirmado_con_error_y_auditoria_sin_conexion_retenida():
    app, conn, pool, fabrica = _app_turnos(error=ErrorApiCalendar(500))
    fila = fila_turno(TENANT)
    conn.turnos[fila["id"]] = fila
    r = await http(app).post(f"/turnos/{fila['id']}/aprobar", headers=_h())
    assert r.status_code == 200
    assert r.json()["status"] == "confirmed"
    assert r.json()["calendario"]["estado"] == "error"
    assert [a[0] for a in conn.auditoria].count("calendar_sync_failed") == 1
    assert fabrica.servicios[0].en_uso_durante_la_llamada == [0]
    assert pool.bloqueos == 0


def _cliente_con_eventos(conn, n=2):
    fila = conn._fila(tenant_id=TENANT, phone="+5491155500101", name="Cliente")
    conn.clientes[fila["id"]] = fila
    conn.targets = [{"integration_id": uuid.uuid4(), "google_calendar_event_id": f"evt-{i}"} for i in range(n)]
    return fila["id"]


def _app_clientes(**kw):
    conn = ConnClientes()
    pool = PoolUno(conn)
    fabrica = _fabrica(pool, **kw)
    app, _, _ = app_clientes(pool=pool, fabrica=fabrica)
    return app, conn, pool, fabrica


async def test_anonimizar_limpia_google_sin_retener_la_conexion_y_registra_los_fallidos():
    app, conn, pool, fabrica = _app_clientes(fallidos=[evento_no_limpiado("evt-1", "token_vencido")])
    cid = _cliente_con_eventos(conn)
    r = await http(app).post(f"/clientes/{cid}/anonimizar", headers=_h())
    assert r.status_code == 200, r.text
    assert r.json()["calendario"] == {"estado": "ok", "eventos": 2, "no_limpiados": 1}
    assert pool.bloqueos == 0
    assert fabrica.servicios[0].en_uso_durante_la_llamada == [0]
    assert [(a[0], json.loads(a[3])) for a in conn.auditoria] == [
        ("calendar_scrub_failed", {"event_id": "evt-1", "motivo": "token_vencido"})
    ]


async def test_anonimizar_con_google_caido_registra_un_fallo_por_evento_sin_retener_la_conexion():
    app, conn, pool, fabrica = _app_clientes(error=RuntimeError("detalle interno"))
    cid = _cliente_con_eventos(conn)
    r = await http(app).post(f"/clientes/{cid}/anonimizar", headers=_h())
    assert r.status_code == 200
    assert r.json()["calendario"] == {"estado": "ok", "eventos": 2, "no_limpiados": 2}
    assert [json.loads(a[3])["motivo"] for a in conn.auditoria] == ["error_inesperado"] * 2
    assert fabrica.servicios[0].en_uso_durante_la_llamada == [0]
    assert pool.bloqueos == 0
