"""Reintento de limpiezas de Calendar contra Postgres real, conectado como `purge_job` (sin grants sobre tablas).

Google es un transporte HTTP simulado (el servicio real de `platform_calendar` corre sobre él); la credencial sale del
Vault real con `get_integration_secret` en el contexto del tenant, y el estado de cada limpieza se deduce de
`audit_log` con las funciones `list_calendar_scrubs_pending` y `record_calendar_scrub_result`.
"""

import json
import uuid
from contextlib import asynccontextmanager

import httpx
import pytest
from platform_calendar import ConfigGoogle, crear_calendario
from platform_secrets import crear_almacen
from turnos_negocio.calendario import TEXTO_EVENTO_ANONIMIZADO

from jobs_purga.reintento_calendar import ConfigReintento
from jobs_purga.reintento_calendar import reintentar_limpiezas as _reintentar_limpiezas

pytestmark = pytest.mark.integracion

asyncpg = pytest.importorskip("asyncpg")

CONFIG_GOOGLE = ConfigGoogle("id-de-prueba", "valor-de-prueba", "https://ejemplo.test/callback")


class PoolDeUnaConexion:
    def __init__(self, conn):
        self._conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self._conn


class Reportador:
    def __init__(self):
        self.mensajes: list[tuple] = []
        self.excepciones: list[tuple] = []

    def capturar_mensaje(self, mensaje, **contexto):
        self.mensajes.append((mensaje, contexto))

    def capturar_excepcion(self, exc, **contexto):
        self.excepciones.append((exc, contexto))


class GoogleSimulado:
    """Responde el token y el PATCH del evento; `estado_evento` decide si limpiar anda (200) o falla."""

    def __init__(self):
        self.estado_evento = 200
        self.eventos_tocados: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "acceso-de-prueba", "expires_in": 3600})
        assert request.method == "PATCH"
        self.eventos_tocados.append(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(self.estado_evento, json={})


@pytest.fixture
async def escenario(db_superuser):
    """Un tenant con un profesional conectado a Calendar (credencial real en Vault) y un fallo de limpieza."""
    sufijo = uuid.uuid4().hex[:8]
    tenant, prof, integracion = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    evento = f"evt-{sufijo}"
    await db_superuser.execute(
        "insert into public.tenants (id, name, slug) values ($1, 'T', $2)", tenant, f"it-scrub-{sufijo}"
    )
    await db_superuser.execute(
        "insert into public.professionals (id, tenant_id, name) values ($1, $2, 'Profesional')", prof, tenant
    )
    secreto = await db_superuser.fetchval(
        "select vault.create_secret($1, $2)", json.dumps({"refresh_token": "refresco-de-prueba"}), f"scrub-{sufijo}"
    )
    await db_superuser.execute(
        "insert into public.tenant_integrations (id, tenant_id, integration_type, professional_id, secret_id) "
        "values ($1, $2, 'google_calendar', $3, $4)",
        integracion,
        tenant,
        prof,
        secreto,
    )

    async def fallo(event_id, integration_id=integracion):
        metadata = {"event_id": event_id, "motivo": "error_api"}
        if integration_id is not None:
            metadata["integration_id"] = str(integration_id)
        await db_superuser.execute(
            "select public.audit_log_append($1, 'system', null, 'calendar_scrub_failed', 'tenants', $1, $2::jsonb)",
            tenant,
            json.dumps(metadata),
        )

    try:
        yield {"tenant": tenant, "integracion": integracion, "evento": evento, "fallo": fallo}
    finally:
        async with db_superuser.transaction():
            await db_superuser.execute("set local session_replication_role = replica")
            await db_superuser.execute("delete from public.audit_log where tenant_id = $1", tenant)
            for tabla in ("tenant_integrations", "professionals"):
                await db_superuser.execute(f"delete from public.{tabla} where tenant_id = $1", tenant)
            await db_superuser.execute("delete from vault.secrets where id = $1", secreto)
            await db_superuser.execute("delete from public.tenants where id = $1", tenant)


@pytest.fixture
async def entorno(conectar_como):
    conn = await conectar_como("purge_job")
    assert await conn.fetchval("select session_user") == "purge_job"
    google = GoogleSimulado()
    http = httpx.AsyncClient(transport=httpx.MockTransport(google))
    yield PoolDeUnaConexion(conn), google, http, Reportador()
    await http.aclose()


def reintentar_limpiezas(*args, **kw):
    return _reintentar_limpiezas(*args, texto_neutro=TEXTO_EVENTO_ANONIMIZADO, **kw)


def _fabrica(http):
    return lambda conn: crear_calendario(CONFIG_GOOGLE, crear_almacen(conn), http)


async def _acciones(db, tenant):
    filas = await db.fetch(
        "select action, metadata from public.audit_log where tenant_id = $1 and action like 'calendar_scrub_%' "
        "order by chain_seq",
        tenant,
    )
    return [
        (f["action"], json.loads(f["metadata"]) if isinstance(f["metadata"], str) else f["metadata"]) for f in filas
    ]


async def test_limpia_con_la_credencial_del_vault_y_registra_resuelta_una_sola_vez(entorno, db_superuser, escenario):
    pool, google, http, reportador = entorno
    await escenario["fallo"](escenario["evento"])

    r = await reintentar_limpiezas(pool, _fabrica(http), reportador)

    assert (r.pendientes, r.resueltas, r.ok) == (1, 1, True)
    assert google.eventos_tocados == [escenario["evento"]]
    acciones = await _acciones(db_superuser, escenario["tenant"])
    assert [a for a, _ in acciones] == ["calendar_scrub_failed", "calendar_scrub_resolved"]

    segunda = await reintentar_limpiezas(pool, _fabrica(http), reportador)  # la resuelta no vuelve a intentarse

    assert segunda.pendientes == 0
    assert google.eventos_tocados == [escenario["evento"]]


async def test_si_google_sigue_fallando_registra_el_reintento_y_alerta_al_llegar_al_umbral(
    entorno, db_superuser, escenario
):
    pool, google, http, reportador = entorno
    google.estado_evento = 500
    await escenario["fallo"](escenario["evento"])

    primera = await reintentar_limpiezas(pool, _fabrica(http), reportador, ConfigReintento(umbral_intentos=3))

    assert (primera.reintentos_fallidos, primera.persistentes, primera.ok) == (1, 0, True)
    assert reportador.mensajes == []
    acciones = await _acciones(db_superuser, escenario["tenant"])
    assert acciones[-1][0] == "calendar_scrub_retry_failed" and acciones[-1][1]["attempt"] == 2

    segunda = await reintentar_limpiezas(pool, _fabrica(http), reportador, ConfigReintento(umbral_intentos=3))

    assert (segunda.persistentes, segunda.ok) == (1, False)
    ((_, contexto),) = reportador.mensajes
    assert contexto["persistentes"] == 1 and contexto["max_intentos"] == 3
    assert escenario["evento"] not in str(contexto) and str(escenario["tenant"]) not in str(contexto)


async def test_sin_integracion_no_se_llama_a_google_y_registra_un_intento_fallido(entorno, db_superuser, escenario):
    pool, google, http, reportador = entorno
    await escenario["fallo"]("evt-huerfano", integration_id=None)

    r = await reintentar_limpiezas(pool, _fabrica(http), reportador)

    assert (r.sin_integracion, r.resueltas) == (1, 0) and r.ok
    assert google.eventos_tocados == []
    assert [a for a, _ in await _acciones(db_superuser, escenario["tenant"])] == [
        "calendar_scrub_failed",
        "calendar_scrub_retry_failed",
    ]


async def test_un_fallo_no_corta_el_resto(entorno, db_superuser, escenario):
    pool, google, http, reportador = entorno
    await escenario["fallo"]("evt-sin-integracion", integration_id=None)
    await escenario["fallo"](escenario["evento"])

    r = await reintentar_limpiezas(pool, _fabrica(http), reportador)

    assert (r.pendientes, r.sin_integracion, r.resueltas) == (2, 1, 1)


async def test_un_job_sin_google_configurado_no_cae(entorno, db_superuser, escenario):
    from jobs_purga.proceso import _CalendarioSinConfigurar

    pool, google, http, reportador = entorno
    await escenario["fallo"](escenario["evento"])

    r = await reintentar_limpiezas(pool, lambda conn: _CalendarioSinConfigurar(), reportador)

    assert (r.sin_configurar, r.ok) == (1, True)
    assert [a for a, _ in await _acciones(db_superuser, escenario["tenant"])] == ["calendar_scrub_failed"]
