"""Tests unitarios del visor de audit_log por tenant."""

import json
import uuid
from datetime import datetime, timezone

import pytest

from platform_admin import DatosInvalidos, TenantNoEncontrado, listar_audit_log
from platform_admin.tests.falsos import ConexionFalsa, PoolFalso

UTC = timezone.utc  # noqa: UP017
ACTOR = uuid.UUID("22222222-2222-2222-2222-222222222222")
TENANT = uuid.UUID("33333333-3333-3333-3333-333333333333")


def _fila(**kw):
    base = {
        "id": uuid.uuid4(),
        "tenant_id": TENANT,
        "actor_type": "platform_admin",
        "actor_id": ACTOR,
        "action": "tenant_create",
        "entity": "tenants",
        "entity_id": TENANT,
        "metadata": json.dumps({"slug": "x"}),
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    return {**base, **kw}


async def test_tenant_inexistente_levanta_tenant_no_encontrado():
    conn = ConexionFalsa()
    with pytest.raises(TenantNoEncontrado):
        await listar_audit_log(PoolFalso(conn), TENANT, actor_id=ACTOR)


async def test_devuelve_entradas_con_metadata_decodificada_y_filtra_por_tenant():
    conn = ConexionFalsa(
        {
            "select id from public.tenants": TENANT,
            "from public.audit_log": [_fila(), _fila(actor_type="staff", action="booking_confirm", metadata=None)],
        }
    )
    entradas = await listar_audit_log(PoolFalso(conn), TENANT, actor_id=ACTOR)

    assert [e.actor_type for e in entradas] == ["platform_admin", "staff"]
    assert entradas[0].metadata == {"slug": "x"} and entradas[1].metadata is None
    sql, args = conn.con("from public.audit_log")[0]
    assert "tenant_id = $1" in sql and args[0] == TENANT
    # no filtra por actor_type por defecto: el visor de plataforma incluye las acciones de platform_admin
    assert "actor_type <>" not in sql


async def test_filtros_opcionales_y_paginacion_van_parametrizados():
    conn = ConexionFalsa({"select id from public.tenants": TENANT})
    desde = datetime(2026, 1, 1, tzinfo=UTC)
    await listar_audit_log(
        PoolFalso(conn), TENANT, action="tenant_create", actor_type="platform_admin", desde=desde, limit=10, offset=20
    )
    sql, args = conn.con("from public.audit_log")[0]
    assert "tenant_create" not in sql and "platform_admin" not in sql  # nunca interpolados
    assert "tenant_create" in args and "platform_admin" in args and desde in args
    assert args[-2:] == (10, 20)


@pytest.mark.parametrize(
    "kw",
    [{"limit": 0}, {"limit": 501}, {"limit": True}, {"offset": -1}, {"actor_type": "otro"}, {"action": ""}],
)
async def test_parametros_invalidos_no_tocan_la_base(kw):
    pool = PoolFalso()
    with pytest.raises(DatosInvalidos):
        await listar_audit_log(pool, TENANT, **kw)
    assert pool.usadas == []
