"""Verificar la cadena de audit_log y descartar una limpieza de Calendar contra Postgres real (rol platform_admin)."""

import uuid
from contextlib import asynccontextmanager

import pytest

pytestmark = pytest.mark.integracion

asyncpg = pytest.importorskip("asyncpg")

from admin_backend.tests.soporte import app_de_prueba, bearer, config_prueba, http  # noqa: E402


class _PoolDeUnaConexion:
    def __init__(self, conn):
        self._conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self._conn


@pytest.fixture
async def tenant(db_superuser):
    tenant = uuid.uuid4()
    await db_superuser.execute(
        "insert into public.tenants (id, name, slug) values ($1, 'Cadena admin', $2)",
        tenant,
        f"it-cadena-adm-{tenant.hex[:8]}",
    )
    try:
        yield tenant
    finally:
        async with db_superuser.transaction():
            await db_superuser.execute("alter table public.audit_log disable trigger audit_log_solo_agregar_row")
            await db_superuser.execute("delete from public.audit_log where tenant_id = $1", tenant)
            await db_superuser.execute("alter table public.audit_log enable trigger audit_log_solo_agregar_row")
            await db_superuser.execute("delete from public.tenants where id = $1", tenant)


@pytest.fixture
async def app(conectar_como):
    admin = await conectar_como("platform_admin")
    app, _, _ = app_de_prueba(pool=_PoolDeUnaConexion(admin))
    return app


async def _escribir(db_superuser, tenant, action, metadata="{}"):
    await db_superuser.execute(
        "insert into public.audit_log (tenant_id, actor_type, action, metadata) values ($1, 'system', $2, $3::jsonb)",
        tenant,
        action,
        metadata,
    )


async def test_cadena_integra_y_cadena_adulterada(app, tenant, db_superuser):
    for i in range(3):
        await _escribir(db_superuser, tenant, f"it_{i}")
    ruta = f"/tenants/{tenant}/audit-log/verificar-cadena"
    async with http(app) as c:
        sana = await c.get(ruta, headers=bearer())
        assert sana.status_code == 200
        assert sana.json() == {
            "tenant_id": str(tenant),
            "integra": True,
            "fila_invalida_id": None,
            "fila_invalida_chain_seq": None,
        }

        # adulteración: se cambia la acción de la fila 2 sin recalcular el hash
        fila_2 = await db_superuser.fetchval(
            "select id from public.audit_log where tenant_id = $1 and chain_seq = 2", tenant
        )
        async with db_superuser.transaction():
            await db_superuser.execute("alter table public.audit_log disable trigger audit_log_solo_agregar_row")
            await db_superuser.execute("update public.audit_log set action = 'adulterada' where id = $1", fila_2)
            await db_superuser.execute("alter table public.audit_log enable trigger audit_log_solo_agregar_row")
        rota = await c.get(ruta, headers=bearer())
    assert rota.status_code == 200
    assert rota.json()["integra"] is False
    assert rota.json()["fila_invalida_id"] == str(fila_2) and rota.json()["fila_invalida_chain_seq"] == 2


async def test_cadena_de_plataforma_y_tenant_inexistente(app, db_superuser):
    async with http(app) as c:
        plataforma = await c.get("/plataforma/audit-log/verificar-cadena", headers=bearer())
        inexistente = await c.get(f"/tenants/{uuid.uuid4()}/audit-log/verificar-cadena", headers=bearer())
    assert plataforma.status_code == 200 and plataforma.json()["tenant_id"] is None
    assert plataforma.json()["integra"] is True
    assert inexistente.status_code == 404


async def test_descartar_limpieza_pendiente_la_deja_descartada_y_dos_veces_da_404(app, tenant, db_superuser):
    await _escribir(db_superuser, tenant, "calendar_scrub_failed", '{"event_id": "evt-adm-1", "motivo": "timeout"}')
    ruta = f"/tenants/{tenant}/limpiezas-calendar/descartar"
    origen = {"Origin": config_prueba().admin_origin}
    cuerpo = {"event_id": "evt-adm-1", "motivo": "el evento ya no existe en Google"}
    async with http(app) as c:
        ok = await c.post(ruta, json=cuerpo, headers={**bearer(), **origen})
        otra_vez = await c.post(ruta, json=cuerpo, headers={**bearer(), **origen})
        sin_motivo = await c.post(ruta, json={"event_id": "evt-adm-1"}, headers={**bearer(), **origen})
    assert ok.status_code == 200 and ok.json()["estado"] == "descartada"
    assert otra_vez.status_code == 404
    assert sin_motivo.status_code == 422
    fila = await db_superuser.fetchrow(
        "select actor_type, actor_id, metadata ->> 'motivo' as motivo from public.audit_log "
        "where tenant_id = $1 and action = 'calendar_scrub_discarded'",
        tenant,
    )
    assert fila["actor_type"] == "platform_admin" and fila["actor_id"] is None
    assert fila["motivo"] == "el evento ya no existe en Google"
