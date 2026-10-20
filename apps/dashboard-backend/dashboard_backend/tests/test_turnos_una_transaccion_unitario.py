"""Crear, mover, aprobar y cancelar un turno usan una sola transacción para la sesión y el cambio.

La conexión se toma una vez para la sesión viva, el permiso, el bloqueo, el cambio, el audit y el aviso encolado; las
etapas posteriores al COMMIT (Calendar, envío del aviso) abren las suyas, cortas. El calendario simulado anota
cuántas conexiones se habían tomado cuando se lo llamó: es el momento exacto en que termina la etapa transaccional.
"""

import pytest
from platform_calendar import AccionSync, ResultadoSync

from dashboard_backend.tests.soporte_turnos import ConnTurnos, PoolTurnos, app_con_pool, fabrica_de, fila_turno
from dashboard_backend.tests.test_turnos_unitario import NUEVO_INICIO, TENANT, _alta_ok, _cuerpo_alta, _h, http


class CalendarioObservador:
    """Anota cuántas conexiones había tomado el pool al sincronizar (o sea, tras el COMMIT del cambio)."""

    def __init__(self, pool: PoolTurnos):
        self.pool = pool
        self.adquisiciones_al_sincronizar: int | None = None

    async def sincronizar_evento(self, profesional_id, evento):
        self.adquisiciones_al_sincronizar = self.pool.adquisiciones
        return ResultadoSync(AccionSync.CREADO, "evt-nuevo")

    async def retirar_evento(self, profesional_id, id_externo):
        self.adquisiciones_al_sincronizar = self.pool.adquisiciones
        return ResultadoSync(AccionSync.BORRADO, None)


def _pool_con_turno(**cambios):
    pool = PoolTurnos(ConnTurnos())
    fila = fila_turno(TENANT, **cambios)
    pool.conn.turnos[fila["id"]] = fila
    return pool, fila


CASOS = [
    ("crear", "post", "/turnos", {"status": "pending"}),
    ("aprobar", "post", "/turnos/{id}/aprobar", {"status": "pending"}),
    ("cancelar", "post", "/turnos/{id}/cancelar", {"status": "confirmed", "google_calendar_event_id": "evt-viejo"}),
    ("mover", "post", "/turnos/{id}/mover", {"status": "confirmed"}),
]


@pytest.mark.parametrize("nombre,metodo,ruta,estado", CASOS, ids=[c[0] for c in CASOS])
async def test_la_sesion_y_el_cambio_comparten_una_transaccion(nombre, metodo, ruta, estado):
    pool, fila = _pool_con_turno(**estado)
    cuerpo = None
    if nombre == "crear":
        _alta_ok(pool.conn)
        cuerpo = _cuerpo_alta()
    if nombre == "mover":
        cuerpo = {"start_at": NUEVO_INICIO.isoformat()}
    calendario = CalendarioObservador(pool)
    app, _, _ = app_con_pool(pool, calendario=calendario, fabrica=fabrica_de(calendario))
    kw = {"json": cuerpo} if cuerpo else {}
    r = await getattr(http(app), metodo)(ruta.format(id=fila["id"]), headers=_h(), **kw)
    assert r.status_code in (200, 201), r.text
    assert calendario.adquisiciones_al_sincronizar == 1
