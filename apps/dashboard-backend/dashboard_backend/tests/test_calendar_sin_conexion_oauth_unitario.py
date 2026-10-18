"""El OAuth de Calendar no retiene conexiones del pool mientras espera a Google.

Pool de UNA conexión y un Google simulado lento: durante el canje del `code` el Google simulado pide una conexión al
mismo pool (si la request siguiera reteniendo la suya, se bloquearía). Datos ficticios; ningún test sale a la red.
"""

import asyncio
import uuid
from contextlib import asynccontextmanager

import httpx
import pytest

from dashboard_backend.tests.soporte import ORIGEN, http, token
from dashboard_backend.tests.soporte_calendar import ConnCalendar, GoogleSimulado, app_calendar

TENANT = uuid.uuid4()
YO = uuid.uuid4()
ESPERA_MAX_SEG = 0.5


class PoolUno:
    """Pool de tamaño 1: una segunda adquisición simultánea espera; si pasa `ESPERA_MAX_SEG`, queda registrada."""

    def __init__(self, conn):
        self.conn = conn
        self._libre = asyncio.Semaphore(1)
        self.en_uso = 0
        self.bloqueos = 0
        self.adquisiciones = 0

    @asynccontextmanager
    async def acquire(self):
        try:
            await asyncio.wait_for(self._libre.acquire(), ESPERA_MAX_SEG)
        except TimeoutError:
            self.bloqueos += 1
            raise
        self.adquisiciones += 1
        self.en_uso += 1
        try:
            yield self.conn
        finally:
            self.en_uso -= 1
            self._libre.release()


class GoogleLento(GoogleSimulado):
    def __init__(self, pool: PoolUno, **kw):
        super().__init__(**kw)
        self.pool = pool
        self.en_uso_al_llamar: list[int] = []

    async def _atender(self, request: httpx.Request) -> httpx.Response:
        self.en_uso_al_llamar.append(self.pool.en_uso)
        await asyncio.sleep(0.05)  # Google tarda
        async with self.pool.acquire():  # otro uso concurrente del pool durante la espera
            pass
        return super()._atender(request)


def _h():
    return {"Cookie": "dash_acceso=" + token(user_id=YO, tenant_id=TENANT), "Origin": ORIGEN}


def _armar(**kw):
    conn = ConnCalendar()
    pool = PoolUno(conn)
    google = GoogleLento(pool, **kw)
    app, _, _ = app_calendar(google=google, pool=pool)
    prof = conn.agregar_profesional(TENANT, vinculado_a=YO)
    conn.estado_staff = {"is_owner": False, "is_active": True, "tiene": False}
    return app, conn, pool, google, prof


async def test_el_callback_canjea_el_code_sin_conexion_tomada_y_guarda_en_otra_transaccion():
    app, conn, pool, google, prof = _armar()
    cuerpo = {"code": "c", "state": google.state_de(TENANT, prof, YO)}
    r = await http(app).post("/calendar/callback", json=cuerpo, headers=_h())
    assert r.status_code == 200, r.text
    assert pool.bloqueos == 0
    assert google.en_uso_al_llamar == [0]  # ninguna conexión tomada mientras se llamaba a Google
    assert conn.integraciones[prof]["secret"] is not None
    assert conn.auditoria == [("connect_google_calendar", prof)]
    assert conn.nonces_consumidos  # el nonce se consumió (y antes del canje)
    sentencias = conn.sentencias()
    assert sentencias.count("BEGIN") == sentencias.count("COMMIT")


async def test_el_nonce_se_consume_y_confirma_antes_de_llamar_a_google():
    app, conn, pool, google, prof = _armar()
    visto = {}
    original = google._atender

    async def _espia(request):
        visto["nonces"] = set(conn.nonces_consumidos)
        visto["abiertas"] = conn.sentencias().count("BEGIN") - conn.sentencias().count("COMMIT")
        return await original(request)

    google._atender = _espia
    cuerpo = {"code": "c", "state": google.state_de(TENANT, prof, YO)}
    r = await http(app).post("/calendar/callback", json=cuerpo, headers=_h())
    assert r.status_code == 200, r.text
    assert len(visto["nonces"]) == 1
    assert visto["abiertas"] == 0


@pytest.mark.parametrize(
    ("kw", "esperado"),
    [({"estado": 400, "cuerpo": {"error": "invalid_grant"}}, 502), ({"red": True}, 503)],
)
async def test_si_google_falla_no_se_retiene_la_conexion_ni_se_guarda(kw, esperado):
    app, conn, pool, google, prof = _armar(**kw)
    cuerpo = {"code": "c", "state": google.state_de(TENANT, prof, YO)}
    r = await http(app).post("/calendar/callback", json=cuerpo, headers=_h())
    assert r.status_code == esperado, r.text
    assert pool.bloqueos == 0 and google.en_uso_al_llamar == [0]
    assert conn.integraciones == {}


async def test_un_replay_no_gasta_un_segundo_code():
    app, conn, pool, google, prof = _armar()
    cuerpo = {"code": "c", "state": google.state_de(TENANT, prof, YO)}
    assert (await http(app).post("/calendar/callback", json=cuerpo, headers=_h())).status_code == 200
    r = await http(app).post("/calendar/callback", json=cuerpo, headers=_h())
    assert r.status_code == 400 and r.json()["detail"]["codigo"] == "state_invalido"
    assert len(google.en_uso_al_llamar) == 1


async def test_iniciar_y_estado_no_dejan_conexion_tomada_al_construir_la_respuesta():
    app, conn, pool, google, prof = _armar()
    r = await http(app).post(f"/profesionales/{prof}/calendar/conexion", headers=_h())
    assert r.status_code == 200, r.text
    assert pool.en_uso == 0 and pool.bloqueos == 0
    r = await http(app).get(f"/profesionales/{prof}/calendar", headers=_h())
    assert r.status_code == 200 and r.json()["google_configurado"] is True
    sentencias = conn.sentencias()
    assert sentencias.count("BEGIN") == sentencias.count("COMMIT")
