"""La copia de la migración de canales del cliente, ejecutada tal cual sobre datos previos.

Lee la sentencia `insert ... select` del archivo de la migración (no una copia escrita en el test), la corre dos
veces y compara fila por fila con lo que debería haber copiado.
"""

import re
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.integracion

asyncpg = pytest.importorskip("asyncpg")

MIGRACION = Path(__file__).resolve().parents[2] / "supabase" / "migrations" / "20260103003400_canales_cliente.sql"


def _sentencia_de_copia() -> str:
    sin_comentarios = "\n".join(x for x in MIGRACION.read_text(encoding="utf-8").splitlines() if not x.startswith("--"))
    m = re.search(r"insert into public\.canales_cliente \(.*?select.*?;", sin_comentarios, re.S | re.I)
    assert m, "no se encontró la sentencia de copia en la migración"
    return m.group(0)


@pytest.fixture
async def escenario(db_superuser):
    tenant = uuid.uuid4()
    await db_superuser.execute(
        "insert into public.tenants (id, name, slug) values ($1, 'Copia', $2)", tenant, f"it-copia-{tenant.hex[:8]}"
    )
    telegram, anonimizada, manual = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await db_superuser.execute(
        "insert into public.end_customers (id, tenant_id, channel, channel_user_id, channel_user_hash, created_at) "
        "values ($1, $4, 'telegram', 'tg-copia', null, '2026-01-05 10:00+00'), "
        "($2, $4, 'telegram', null, 'hash-copia', '2026-01-06 10:00+00'), "
        "($3, $4, null, null, null, '2026-01-07 10:00+00')",
        telegram,
        anonimizada,
        manual,
        tenant,
    )
    try:
        yield tenant, telegram
    finally:
        await db_superuser.execute("delete from public.end_customers where tenant_id = $1", tenant)
        await db_superuser.execute("delete from public.tenants where id = $1", tenant)


async def _filas(conn, tenant):
    return [
        tuple(r)
        for r in await conn.fetch(
            "select end_customer_id, canal, identificador, verificado_at, created_at "
            "from public.canales_cliente where tenant_id = $1 order by identificador",
            tenant,
        )
    ]


async def test_la_copia_de_la_migracion_es_identica_y_re_ejecutable(db_superuser, escenario):
    tenant, telegram = escenario
    sentencia = _sentencia_de_copia()
    await db_superuser.execute("delete from public.canales_cliente where tenant_id = $1", tenant)

    fecha = await db_superuser.fetchval("select '2026-01-05 10:00+00'::timestamptz")
    esperado = [(telegram, "telegram", "tg-copia", fecha, fecha)]

    await db_superuser.execute(sentencia)
    assert await _filas(db_superuser, tenant) == esperado

    await db_superuser.execute(sentencia)
    assert await _filas(db_superuser, tenant) == esperado

    # la ficha original no se tocó
    canal_viejo = await db_superuser.fetchval(
        "select channel_user_id from public.end_customers where id = $1", telegram
    )
    assert canal_viejo == "tg-copia"
