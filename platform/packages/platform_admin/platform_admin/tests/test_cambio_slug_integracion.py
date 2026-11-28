"""Cambio de slug contra Postgres real, conectado como el rol `platform_admin`: historial, redirección y auditoría."""

import asyncio
import json
import uuid
from contextlib import asynccontextmanager

import pytest

from platform_admin import (
    CambioSlugConcurrente,
    PurgaEnCurso,
    SlugEnUso,
    SlugInvalido,
    SlugReservado,
    SlugSinCambio,
    TenantNoEncontrado,
    cambiar_slug,
)

pytestmark = pytest.mark.integracion

asyncpg = pytest.importorskip("asyncpg")

ACTOR = uuid.uuid4()


class PoolDeUnaConexion:
    """Adapta una conexión de `conectar_como` a la interfaz de pool que usa `platform_tenancy`."""

    def __init__(self, conn):
        self._conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self._conn


def _slug(prefijo="cs"):
    return f"{prefijo}-{uuid.uuid4().hex[:12]}"


@pytest.fixture
async def pool(conectar_como):
    return PoolDeUnaConexion(await conectar_como("platform_admin"))


@pytest.fixture
async def crear_tenant(db_superuser):
    """Fábrica de tenants sembrados por el superusuario; al terminar limpia su audit y sus tenants."""
    creados: list[uuid.UUID] = []

    async def _crear(slug=None, status="active"):
        tenant_id = uuid.uuid4()
        await db_superuser.execute(
            "insert into public.tenants (id, name, slug, status) values ($1, 'Negocio', $2, $3)",
            tenant_id,
            slug or _slug(),
            status,
        )
        creados.append(tenant_id)
        return tenant_id

    try:
        yield _crear
    finally:
        async with db_superuser.transaction():
            # Limpieza sin triggers (audit_log solo-agregar): solo vale dentro de esta transacción
            await db_superuser.execute("set local session_replication_role = replica")
            await db_superuser.execute("delete from public.audit_log where tenant_id = any($1::uuid[])", creados)
            await db_superuser.execute("delete from public.slugs_anteriores where tenant_id = any($1::uuid[])", creados)
            await db_superuser.execute("delete from public.tenants where id = any($1::uuid[])", creados)


async def _slug_de(db, tenant_id):
    return await db.fetchval("select slug from public.tenants where id = $1", tenant_id)


async def _resolver(db, slug):
    filas = await db.fetch("select * from public.resolver_slug_tenant($1)", slug)
    return [dict(f) for f in filas]


async def test_cambiar_slug_guarda_el_viejo_180_dias_deja_audit_con_el_actor_y_redirige(
    pool, db_superuser, crear_tenant
):
    viejo = _slug("viejo")
    tenant = await crear_tenant(viejo)
    nuevo = _slug("nuevo")

    res = await cambiar_slug(pool, tenant, nuevo, actor_id=ACTOR)

    assert (res.slug_anterior, res.slug_nuevo) == (viejo, nuevo)
    assert await _slug_de(db_superuser, tenant) == nuevo
    fila = await db_superuser.fetchrow(
        "select (vigente_hasta - reemplazado_at) as ventana from public.slugs_anteriores where slug = $1", viejo
    )
    assert fila["ventana"].days == 180
    audit = await db_superuser.fetchrow(
        "select actor_type, actor_id, entity, entity_id, metadata from public.audit_log "
        "where tenant_id = $1 and action = 'tenant_slug_change'",
        tenant,
    )
    assert audit["actor_type"] == "platform_admin" and audit["actor_id"] == ACTOR
    assert audit["entity"] == "tenants" and audit["entity_id"] == tenant
    assert json.loads(audit["metadata"]) == {"slug_anterior": viejo, "slug_nuevo": nuevo}
    # el link viejo redirige al nuevo; el nuevo resuelve directo
    assert await _resolver(db_superuser, viejo) == [
        {"tenant_id": tenant, "nombre": "Negocio", "slug_vigente": nuevo, "reemplazado": True}
    ]
    assert [f["reemplazado"] for f in await _resolver(db_superuser, nuevo)] == [False]


async def test_aislamiento_el_slug_viejo_de_a_no_resuelve_a_b_ni_b_lo_puede_tomar(pool, db_superuser, crear_tenant):
    viejo_a = _slug("viejo-a")
    a = await crear_tenant(viejo_a)
    b = await crear_tenant()
    await cambiar_slug(pool, a, _slug("nuevo-a"), actor_id=ACTOR)

    assert [f["tenant_id"] for f in await _resolver(db_superuser, viejo_a)] == [a]
    with pytest.raises(SlugEnUso):
        await cambiar_slug(pool, b, viejo_a, actor_id=ACTOR)  # enlace anterior vigente de otro negocio
    assert [f["tenant_id"] for f in await _resolver(db_superuser, viejo_a)] == [a]
    assert (
        await db_superuser.fetchval(
            "select count(*) from public.audit_log where tenant_id = $1 and action = 'tenant_slug_change'", b
        )
        == 0
    )


async def test_el_negocio_puede_volver_a_su_propio_slug_anterior(pool, db_superuser, crear_tenant):
    original = _slug("orig")
    tenant = await crear_tenant(original)
    await cambiar_slug(pool, tenant, _slug("otro"), actor_id=ACTOR)

    await cambiar_slug(pool, tenant, original, actor_id=ACTOR)

    assert await _slug_de(db_superuser, tenant) == original
    assert [f["reemplazado"] for f in await _resolver(db_superuser, original)] == [False]


async def test_slug_de_otro_tenant_es_slug_en_uso_y_no_cambia_nada(pool, db_superuser, crear_tenant):
    ocupado = _slug("ocupado")
    await crear_tenant(ocupado)
    tenant = await crear_tenant()
    antes = await _slug_de(db_superuser, tenant)

    with pytest.raises(SlugEnUso):
        await cambiar_slug(pool, tenant, ocupado, actor_id=ACTOR)

    assert await _slug_de(db_superuser, tenant) == antes
    assert await db_superuser.fetchval("select count(*) from public.slugs_anteriores where tenant_id = $1", tenant) == 0
    assert (
        await db_superuser.fetchval(
            "select count(*) from public.audit_log where tenant_id = $1 and action = 'tenant_slug_change'", tenant
        )
        == 0
    )


@pytest.mark.parametrize(
    ("slug", "error"),
    [("admin", SlugReservado), ("Mayus", SlugInvalido), ("ab", SlugInvalido), ("a" * 41, SlugInvalido)],
)
async def test_reservado_formato_y_largo_se_rechazan_sin_tocar_nada(pool, db_superuser, crear_tenant, slug, error):
    tenant = await crear_tenant()
    antes = await _slug_de(db_superuser, tenant)

    with pytest.raises(error):
        await cambiar_slug(pool, tenant, slug, actor_id=ACTOR)

    assert await _slug_de(db_superuser, tenant) == antes


async def test_el_mismo_slug_vigente_y_un_tenant_inexistente_se_rechazan(pool, db_superuser, crear_tenant):
    tenant = await crear_tenant()
    with pytest.raises(SlugSinCambio):
        await cambiar_slug(pool, tenant, await _slug_de(db_superuser, tenant), actor_id=ACTOR)
    with pytest.raises(TenantNoEncontrado):
        await cambiar_slug(pool, uuid.uuid4(), _slug(), actor_id=ACTOR)


async def test_un_tenant_inactivo_se_puede_renombrar_y_uno_en_purga_no(pool, db_superuser, crear_tenant):
    inactivo = await crear_tenant()
    await db_superuser.execute(
        "update public.tenants set status = 'inactive', deactivated_at = now() where id = $1", inactivo
    )
    await cambiar_slug(pool, inactivo, _slug(), actor_id=ACTOR)  # la base solo prohíbe purging y purged

    en_purga = await crear_tenant()
    await db_superuser.execute(
        "update public.tenants set status = 'inactive', deactivated_at = now() - interval '1 year' where id = $1",
        en_purga,
    )
    await db_superuser.execute("update public.tenants set status = 'purging' where id = $1", en_purga)
    with pytest.raises(PurgaEnCurso):
        await cambiar_slug(pool, en_purga, _slug(), actor_id=ACTOR)


async def test_dos_cambios_a_la_vez_al_mismo_slug_uno_gana_y_el_otro_es_4xx_nunca_500(
    conectar_como, db_superuser, crear_tenant
):
    a = await crear_tenant()
    b = await crear_tenant()
    codigo = _slug("carrera")
    pool_a = PoolDeUnaConexion(await conectar_como("platform_admin"))
    pool_b = PoolDeUnaConexion(await conectar_como("platform_admin"))

    resultados = await asyncio.gather(
        cambiar_slug(pool_a, a, codigo, actor_id=ACTOR),
        cambiar_slug(pool_b, b, codigo, actor_id=ACTOR),
        return_exceptions=True,
    )

    ganadores = [r for r in resultados if not isinstance(r, Exception)]
    perdedores = [r for r in resultados if isinstance(r, Exception)]
    assert len(ganadores) == 1 and len(perdedores) == 1
    assert isinstance(perdedores[0], (SlugEnUso, CambioSlugConcurrente))
    assert await db_superuser.fetchval("select count(*) from public.tenants where slug = $1", codigo) == 1
