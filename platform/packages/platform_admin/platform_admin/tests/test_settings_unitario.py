"""Tests unitarios de platform_settings: validación sin tocar la base y auditoría con tenant_id null."""

import json
import uuid

import pytest

from platform_admin import SettingInvalido, editar_setting, listar_settings
from platform_admin.tests.falsos import ConexionFalsa, PoolFalso

ACTOR = uuid.UUID("22222222-2222-2222-2222-222222222222")
CLAVE = "tenant_deletion_grace_period_days"


async def test_listar_settings_devuelve_clave_valor_con_el_json_ya_decodificado():
    conn = ConexionFalsa({"from public.platform_settings": [{"key": CLAVE, "value": "90"}]})
    assert await listar_settings(PoolFalso(conn), actor_id=ACTOR) == {CLAVE: 90}


async def test_editar_setting_guarda_el_valor_y_audita_sin_tenant():
    conn = ConexionFalsa({"select value from public.platform_settings": "90"})
    res = await editar_setting(PoolFalso(conn), CLAVE, 120, actor_id=ACTOR)

    assert res == {"key": CLAVE, "desde": 90, "hasta": 120}
    upsert = conn.con("insert into public.platform_settings")[0]
    assert "on conflict" in upsert[0].lower()
    assert upsert[1][0] == CLAVE and json.loads(upsert[1][1]) == 120
    (auditoria,) = conn.auditorias()
    accion, entidad, entidad_id, metadata, tenant_id = auditoria
    assert (accion, entidad, entidad_id, tenant_id) == ("platform_settings_update", "platform_settings", None, None)
    assert json.loads(metadata) == {"key": CLAVE, "desde": 90, "hasta": 120}
    assert conn.sqls()[0] == "BEGIN" and conn.sqls()[-1] == "COMMIT"


async def test_editar_setting_sin_valor_previo_audita_desde_null():
    conn = ConexionFalsa()
    res = await editar_setting(PoolFalso(conn), CLAVE, 30, actor_id=ACTOR)
    assert res["desde"] is None and res["hasta"] == 30


@pytest.mark.parametrize("valor", [0, -1, -90, 1.5, 90.0, "90", True, False, None, [90], {"d": 90}])
async def test_periodo_de_gracia_exige_entero_positivo_y_no_toca_la_base(valor):
    pool = PoolFalso()  # sin conexiones: cualquier acquire reventaría con IndexError
    with pytest.raises(SettingInvalido, match=CLAVE):
        await editar_setting(pool, CLAVE, valor, actor_id=ACTOR)
    assert pool.usadas == []


async def test_clave_desconocida_se_rechaza_sin_tocar_la_base():
    pool = PoolFalso()
    with pytest.raises(SettingInvalido, match="clave"):
        await editar_setting(pool, "otra_clave", 5, actor_id=ACTOR)
    assert pool.usadas == []
