"""Proveedor de Calendar de larga vida: un cliente HTTP por app, caché de tokens por proceso y config al arrancar."""

import json
import secrets
import uuid

import httpx
import pytest
from platform_auth import VerificadorJWT
from platform_calendar import ConfigGoogle, ErrorConfiguracionCalendar
from platform_secrets import Secreto

from dashboard_backend import calendario as cal
from dashboard_backend.app import crear_app
from dashboard_backend.calendario import ProveedorCalendario, config_google_desde_entorno
from dashboard_backend.tests.soporte import AUD, SECRETO, PoolFalso, SupabaseSimulado, config_prueba

VARIABLES = (
    "GOOGLE_OAUTH_CLIENT_ID",
    "GOOGLE_OAUTH_CLIENT_SECRET",
    "GOOGLE_OAUTH_REDIRECT_URI",
    "CALENDAR_OAUTH_STATE_SECRET",
)
INTEGRACION = uuid.uuid4()


def _config() -> ConfigGoogle:
    return ConfigGoogle(
        client_id="cid-" + secrets.token_hex(4),
        client_secret=secrets.token_urlsafe(16),
        redirect_uri="https://api.ejemplo.test/calendar/callback",
        state_secret=secrets.token_bytes(32),
    )


class _Almacen:
    async def obtener(self, integration_id):
        return Secreto(json.dumps({"refresh_token": "refresco-" + secrets.token_hex(6)}))


def _verificador():
    return VerificadorJWT(algoritmos=["HS256"], audiencia=AUD, secreto=SECRETO)


@pytest.fixture
def clientes(monkeypatch):
    """Cuenta los `httpx.AsyncClient` que crea el proveedor; el transporte es simulado y cuenta los pedidos de token."""
    creados: list[httpx.AsyncClient] = []
    pedidos: list[httpx.Request] = []
    original = httpx.AsyncClient

    def atender(request):
        pedidos.append(request)
        return httpx.Response(200, json={"access_token": "acceso-" + secrets.token_hex(6), "expires_in": 3600})

    class Contador(original):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(atender)
            super().__init__(*args, **kwargs)
            creados.append(self)

    monkeypatch.setattr(cal.httpx, "AsyncClient", Contador)
    monkeypatch.setattr(cal, "crear_almacen", lambda conn: _Almacen())
    return creados, pedidos


async def test_un_solo_cliente_http_para_todas_las_operaciones_y_se_cierra_al_apagar(clientes):
    creados, _ = clientes
    proveedor = ProveedorCalendario(_config())
    await proveedor.abrir()
    for _ in range(5):
        async with proveedor(object()) as servicio:
            await servicio.token_de_acceso(INTEGRACION)
    assert len(creados) == 1
    assert not creados[0].is_closed
    await proveedor.cerrar()
    assert creados[0].is_closed


async def test_dos_operaciones_sobre_la_misma_integracion_hacen_una_sola_llamada_de_token(clientes):
    _, pedidos = clientes
    proveedor = ProveedorCalendario(_config())
    await proveedor.abrir()
    for _ in range(2):
        async with proveedor(object()) as servicio:
            await servicio.token_de_acceso(INTEGRACION)
    assert len(pedidos) == 1
    await proveedor.cerrar()


async def test_token_vencido_o_dentro_del_margen_se_vuelve_a_pedir(clientes):
    _, pedidos = clientes
    ahora = [1000.0]
    proveedor = ProveedorCalendario(_config(), reloj=lambda: ahora[0])
    await proveedor.abrir()

    async def operar():
        async with proveedor(object()) as servicio:
            await servicio.token_de_acceso(INTEGRACION)

    await operar()
    ahora[0] = 4000.0  # sigue vigente (3600 s menos 60 de margen desde 1000)
    await operar()
    assert len(pedidos) == 1
    ahora[0] = 4600.0  # dentro del margen
    await operar()
    assert len(pedidos) == 2
    await proveedor.cerrar()


async def test_el_lifespan_de_la_app_abre_y_cierra_el_cliente(clientes):
    creados, _ = clientes
    app = crear_app(
        config_prueba(),
        verificador=_verificador(),
        pool=PoolFalso(),
        cliente_auth=SupabaseSimulado().cliente(),
        config_google=_config(),
        configurar_observabilidad=False,
    )
    antes = len(creados)  # el cliente de Supabase Auth del test también es un AsyncClient
    async with app.router.lifespan_context(app):
        propios = creados[antes:]
        for _ in range(3):
            async with app.state.calendario(object()) as servicio:
                await servicio.token_de_acceso(INTEGRACION)
        assert len(creados[antes:]) == 1 and not propios[0].is_closed
    assert propios[0].is_closed


async def test_sin_config_el_proveedor_es_calendar_no_configurado():
    proveedor = ProveedorCalendario(None)
    assert proveedor.configurado is False
    with pytest.raises(ErrorConfiguracionCalendar):
        async with proveedor(object()):
            pass


def test_sin_ninguna_variable_arranca_sin_calendar(monkeypatch):
    for v in VARIABLES:
        monkeypatch.delenv(v, raising=False)
    assert config_google_desde_entorno() is None


def test_con_todas_las_variables_devuelve_la_config(monkeypatch):
    for v in VARIABLES:
        monkeypatch.setenv(v, "valor-de-prueba-0123456789")
    config = config_google_desde_entorno()
    assert config is not None and config.state_secret is not None


@pytest.mark.parametrize("faltante", VARIABLES)
def test_variables_a_medias_no_arrancan_y_el_error_nombra_la_variable_sin_su_valor(monkeypatch, faltante):
    for v in VARIABLES:
        monkeypatch.setenv(v, "secreto-que-no-debe-aparecer-123")
    monkeypatch.delenv(faltante)
    with pytest.raises(ErrorConfiguracionCalendar) as e:
        config_google_desde_entorno()
    assert faltante in str(e.value)
    assert "secreto-que-no-debe-aparecer" not in str(e.value)


def test_clave_del_state_demasiado_corta_no_arranca(monkeypatch):
    for v in VARIABLES:
        monkeypatch.setenv(v, "valor-de-prueba-0123456789")
    monkeypatch.setenv("CALENDAR_OAUTH_STATE_SECRET", "clave-corta")
    with pytest.raises(ErrorConfiguracionCalendar, match="CALENDAR_OAUTH_STATE_SECRET") as e:
        config_google_desde_entorno()
    assert "clave-corta" not in str(e.value)


def _preparar_main(monkeypatch):
    from dashboard_backend import main

    monkeypatch.setattr(main.Config, "desde_entorno", classmethod(lambda cls, env: config_prueba()))
    monkeypatch.setattr(main.VerificadorJWT, "desde_entorno", classmethod(lambda cls, env: _verificador()))
    monkeypatch.setattr(main.SupabaseAuthAdmin, "desde_entorno", classmethod(lambda cls, env=None: object()))
    return main


async def test_el_entorno_se_lee_al_arrancar_y_no_dentro_del_request(monkeypatch, clientes):
    main = _preparar_main(monkeypatch)
    for v in VARIABLES:
        monkeypatch.setenv(v, "valor-de-prueba-0123456789")
    app = main.crear_app_desde_entorno()
    for v in VARIABLES:  # el entorno cambia después del arranque
        monkeypatch.delenv(v)

    def _no_debe_llamarse(cls, **kwargs):
        raise AssertionError("ConfigGoogle.desde_entorno dentro de un request")

    monkeypatch.setattr(ConfigGoogle, "desde_entorno", classmethod(_no_debe_llamarse))
    proveedor = app.state.calendario
    assert proveedor.configurado is True
    await proveedor.abrir()
    async with proveedor(object()) as servicio:
        await servicio.token_de_acceso(INTEGRACION)
    await proveedor.cerrar()


def test_main_no_arranca_con_variables_a_medias(monkeypatch):
    main = _preparar_main(monkeypatch)
    for v in VARIABLES:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "x")
    with pytest.raises(ErrorConfiguracionCalendar, match="GOOGLE_OAUTH_CLIENT_SECRET"):
        main.crear_app_desde_entorno()
