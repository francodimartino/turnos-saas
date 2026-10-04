"""Tests unitarios del tracking manual de pago (tenants.paid_until / billing_notes)."""

import json
import uuid
from datetime import date, datetime

import pytest

from platform_admin import DatosInvalidos, TenantNoEncontrado, editar_facturacion, obtener_facturacion
from platform_admin.tests.falsos import ConexionFalsa, PoolFalso

ACTOR = uuid.UUID("22222222-2222-2222-2222-222222222222")
TENANT = uuid.UUID("33333333-3333-3333-3333-333333333333")


def _conn(existe=True):
    fila = {"id": TENANT, "paid_until": date(2026, 1, 1), "billing_notes": "viejo"}
    return ConexionFalsa({"from public.tenants": fila if existe else None})


async def test_edita_ambos_campos_y_audita_sin_el_texto_de_las_notas():
    conn = _conn()
    nota = f"nota-{uuid.uuid4().hex}"
    res = await editar_facturacion(
        PoolFalso(conn), TENANT, paid_until=date(2026, 6, 30), billing_notes=nota, actor_id=ACTOR
    )

    assert res.paid_until == date(2026, 6, 30) and res.billing_notes == nota
    update = conn.con("update public.tenants")[0]
    assert update[1] == (TENANT, date(2026, 6, 30), nota)
    (auditoria,) = conn.auditorias()
    assert auditoria[0] == "tenant_billing_update" and auditoria[4] == TENANT
    metadata = json.loads(auditoria[3])
    assert metadata["campos"] == ["paid_until", "billing_notes"] and metadata["paid_until"] == "2026-06-30"
    assert nota not in auditoria[3]


async def test_editar_solo_un_campo_deja_el_otro_como_estaba():
    conn = _conn()
    res = await editar_facturacion(PoolFalso(conn), TENANT, billing_notes="solo notas", actor_id=ACTOR)
    assert res.paid_until == date(2026, 1, 1) and res.billing_notes == "solo notas"
    assert conn.con("update public.tenants")[0][1] == (TENANT, date(2026, 1, 1), "solo notas")


async def test_none_explicito_borra_el_campo():
    conn = _conn()
    res = await editar_facturacion(PoolFalso(conn), TENANT, paid_until=None, actor_id=ACTOR)
    assert res.paid_until is None and res.billing_notes == "viejo"


async def test_sin_campos_a_editar_se_rechaza():
    pool = PoolFalso()
    with pytest.raises(DatosInvalidos):
        await editar_facturacion(pool, TENANT, actor_id=ACTOR)
    assert pool.usadas == []


@pytest.mark.parametrize(
    "kw",
    [
        {"paid_until": datetime(2026, 1, 1)},
        {"paid_until": "2026-01-01"},
        {"billing_notes": 5},
        {"billing_notes": "x" * 2001},
    ],
)
async def test_valores_invalidos_no_tocan_la_base(kw):
    pool = PoolFalso()
    with pytest.raises(DatosInvalidos):
        await editar_facturacion(pool, TENANT, actor_id=ACTOR, **kw)
    assert pool.usadas == []


async def test_tenant_inexistente():
    with pytest.raises(TenantNoEncontrado):
        await editar_facturacion(PoolFalso(_conn(existe=False)), TENANT, billing_notes="x", actor_id=ACTOR)
    with pytest.raises(TenantNoEncontrado):
        await obtener_facturacion(PoolFalso(ConexionFalsa()), TENANT)


async def test_obtener_facturacion():
    res = await obtener_facturacion(PoolFalso(_conn()), TENANT)
    assert res.tenant_id == TENANT and res.billing_notes == "viejo"
