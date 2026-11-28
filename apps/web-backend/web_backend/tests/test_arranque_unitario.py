"""Arranque del servicio: pool propio con el tamaño de `TAMANOS_POOL`, cierre ordenado y fábrica de producción."""

import pytest
from turnos_almacenamiento import ClienteAlmacenamiento

from web_backend import app as modulo_app
from web_backend import main
from web_backend.config import ConfigInvalida
from web_backend.tests.soporte import AlmacenamientoFalso, PoolFalso, config_prueba, http

ENTORNO = {"WEB_DATABASE_URL": "postgresql://web_reservas@127.0.0.1/db", "WEB_ORIGIN": "https://agendemos.ar"}


@pytest.fixture(autouse=True)
def _sin_observabilidad_real(monkeypatch):
    """La fábrica de producción configura logs y Sentry: en estos tests no se toca el logger raíz."""
    monkeypatch.setattr(modulo_app, "configurar_logging", lambda: None)
    monkeypatch.setattr(modulo_app, "iniciar_sentry", lambda dsn, servicio: False)


class PoolCerrable(PoolFalso):
    def __init__(self):
        super().__init__()
        self.cerrado = False

    async def close(self):
        self.cerrado = True


async def test_al_arrancar_crea_el_pool_del_servicio_web_y_al_cerrar_lo_cierra(monkeypatch):
    pool = PoolCerrable()
    creaciones: list[tuple] = []

    async def crear_pool_falso(dsn, **kw):
        creaciones.append((dsn, kw))
        return pool

    monkeypatch.setattr(modulo_app, "crear_pool", crear_pool_falso)
    almacenamiento = AlmacenamientoFalso()
    config = config_prueba(database_url="postgresql://web_reservas@127.0.0.1/db", statement_cache_size=0)
    app = modulo_app.crear_app(config, almacenamiento=almacenamiento, configurar_observabilidad=False)
    async with app.router.lifespan_context(app):
        r = await http(app).get("/health")
        assert r.status_code == 200
        assert not pool.cerrado
    assert creaciones == [("postgresql://web_reservas@127.0.0.1/db", {"servicio": "web", "statement_cache_size": 0})]
    assert pool.cerrado and almacenamiento.cerrado


async def test_un_pool_inyectado_no_se_crea_ni_se_cierra():
    pool = PoolCerrable()
    app = modulo_app.crear_app(config_prueba(), pool=pool, configurar_observabilidad=False)
    async with app.router.lifespan_context(app):
        pass
    assert not pool.cerrado


async def test_sin_pool_inicializado_health_es_503():
    app = modulo_app.crear_app(config_prueba(), configurar_observabilidad=False)
    r = await http(app).get("/health")
    assert r.status_code == 503 and r.json() == {"estado": "no_disponible"}


def test_la_fabrica_de_produccion_falla_al_arrancar_si_la_configuracion_es_invalida(monkeypatch):
    monkeypatch.setattr(main.os, "environ", {**ENTORNO, "WEB_ORIGIN": "*"})
    with pytest.raises(ConfigInvalida, match="WEB_ORIGIN"):
        main.crear_app_desde_entorno()


def test_la_fabrica_de_produccion_sin_storage_deja_el_logo_apagado(monkeypatch):
    monkeypatch.setattr(main.os, "environ", ENTORNO)
    app = main.crear_app_desde_entorno()
    assert app.state.almacenamiento is None
    assert app.state.config.web_origin == "https://agendemos.ar"


def test_la_fabrica_de_produccion_con_storage_arma_el_cliente_con_su_plazo(monkeypatch):
    monkeypatch.setattr(
        main.os,
        "environ",
        {**ENTORNO, "SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "clave-de-prueba"},
    )
    app = main.crear_app_desde_entorno()
    assert isinstance(app.state.almacenamiento, ClienteAlmacenamiento)
    assert app.state.almacenamiento._plazo_total == main.TIMEOUT_STORAGE_SEG  # noqa: SLF001
    assert 0 < main.TIMEOUT_STORAGE_SEG <= 5
