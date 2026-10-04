"""Humo: la base local está arriba, con migraciones, y el rol dashboard_service entra."""

import pytest

pytestmark = pytest.mark.integracion


async def test_select_1_como_dashboard_service(conectar_como):
    conn = await conectar_como("dashboard_service")
    assert await conn.fetchval("select 1") == 1
    assert await conn.fetchval("select current_user") == "dashboard_service"


async def test_superusuario_ve_tablas_de_plataforma(db_superuser):
    assert await db_superuser.fetchval("select to_regclass('public.tenants') is not null")


@pytest.mark.parametrize(
    "rol",
    ["bot_service", "dashboard_service", "platform_admin", "platform_admin_readonly", "purge_job", "scheduler_job"],
)
async def test_cada_rol_de_login_se_conecta(conectar_como, rol):
    conn = await conectar_como(rol)
    assert await conn.fetchval("select current_user") == rol
