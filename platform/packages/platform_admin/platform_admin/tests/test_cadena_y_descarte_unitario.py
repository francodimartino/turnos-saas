"""Verificación de cadena de audit_log y descarte de limpiezas de Calendar: dominio sin base real."""

import uuid

import pytest

from platform_admin import (
    DatosInvalidos,
    LimpiezaNoPendiente,
    TenantNoEncontrado,
    descartar_limpieza_calendar,
    verificar_cadena_audit,
)
from platform_admin.tests.falsos import ConexionFalsa, PoolFalso

ACTOR = uuid.UUID("22222222-2222-2222-2222-222222222222")
TENANT = uuid.UUID("33333333-3333-3333-3333-333333333333")


class _ErrorSql(Exception):
    def __init__(self, sqlstate):
        super().__init__("error")
        self.sqlstate = sqlstate


async def test_cadena_integra_no_devuelve_fila_invalida():
    conn = ConexionFalsa({"select id from public.tenants": TENANT})
    res = await verificar_cadena_audit(PoolFalso(conn), TENANT, actor_id=ACTOR)
    assert res.integra is True and res.tenant_id == TENANT
    assert res.fila_invalida_id is None and res.fila_invalida_chain_seq is None
    sql, args = conn.con("verify_audit_chain")[0]
    assert "$1" in sql and args == (TENANT,)
    assert not conn.con("chain_seq from public.audit_log")


async def test_cadena_adulterada_devuelve_id_y_posicion_de_la_primera_fila_invalida():
    fila = uuid.uuid4()
    conn = ConexionFalsa(
        {"select id from public.tenants": TENANT, "verify_audit_chain": fila, "chain_seq from public.audit_log": 7}
    )
    res = await verificar_cadena_audit(PoolFalso(conn), TENANT, actor_id=ACTOR)
    assert res.integra is False
    assert res.fila_invalida_id == fila and res.fila_invalida_chain_seq == 7
    assert conn.con("chain_seq from public.audit_log")[0][1] == (fila,)


async def test_cadena_de_plataforma_usa_tenant_null_y_no_busca_tenant():
    conn = ConexionFalsa()
    res = await verificar_cadena_audit(PoolFalso(conn), None, actor_id=ACTOR)
    assert res.integra is True and res.tenant_id is None
    assert conn.con("verify_audit_chain")[0][1] == (None,)
    assert not conn.con("public.tenants")


async def test_cadena_de_tenant_inexistente_levanta_tenant_no_encontrado():
    with pytest.raises(TenantNoEncontrado):
        await verificar_cadena_audit(PoolFalso(ConexionFalsa()), TENANT, actor_id=ACTOR)


async def test_descarte_llama_a_la_funcion_con_tenant_evento_y_motivo():
    conn = ConexionFalsa()
    await descartar_limpieza_calendar(PoolFalso(conn), TENANT, "evt-1", "  evento borrado a mano  ", actor_id=ACTOR)
    sql, args = conn.con("discard_calendar_scrub")[0]
    assert "$1" in sql and "$2" in sql and "$3" in sql
    assert args == (TENANT, "evt-1", "evento borrado a mano")


async def test_descarte_de_evento_no_pendiente_levanta_limpieza_no_pendiente():
    conn = ConexionFalsa(falla_en=("discard_calendar_scrub", _ErrorSql("P0002")))
    with pytest.raises(LimpiezaNoPendiente):
        await descartar_limpieza_calendar(PoolFalso(conn), TENANT, "evt-1", "motivo", actor_id=ACTOR)


async def test_descarte_con_datos_invalidos_de_la_base_levanta_datos_invalidos():
    conn = ConexionFalsa(falla_en=("discard_calendar_scrub", _ErrorSql("22023")))
    with pytest.raises(DatosInvalidos):
        await descartar_limpieza_calendar(PoolFalso(conn), TENANT, "evt-1", "motivo", actor_id=ACTOR)


async def test_descarte_con_otro_error_de_base_se_propaga():
    conn = ConexionFalsa(falla_en=("discard_calendar_scrub", _ErrorSql("XX000")))
    with pytest.raises(_ErrorSql):
        await descartar_limpieza_calendar(PoolFalso(conn), TENANT, "evt-1", "motivo", actor_id=ACTOR)


@pytest.mark.parametrize(
    ("evento", "motivo"),
    [(None, "m"), ("", "m"), ("  ", "m"), ("e", None), ("e", ""), ("e", "   "), (5, "m"), ("e", 5), ("x" * 1025, "m")],
)
async def test_descarte_sin_datos_validos_no_toca_la_base(evento, motivo):
    pool = PoolFalso()
    with pytest.raises(DatosInvalidos):
        await descartar_limpieza_calendar(pool, TENANT, evento, motivo, actor_id=ACTOR)
    assert pool.usadas == []
