"""Chequeos estáticos (sin base de datos) de las migraciones del estado 'purging' y la gracia mínima.

- La migración de plataforma es idéntica a su plantilla; la de producto (purge_tenant_data) no tiene plantilla.
- Las funciones son SECURITY DEFINER con search_path vacío y EXECUTE solo para purge_job.
- Las transiciones permiten inactive -> purging -> purged y no dejan salir de purging hacia atrás.
"""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
NOMBRE = "20260103001500_tenant_purging.sql"
NOMBRE_PRODUCTO = "20260103001600_purga_desde_purging.sql"
MIGRACION = RAIZ / "supabase" / "migrations" / NOMBRE
PRODUCTO = RAIZ / "supabase" / "migrations" / NOMBRE_PRODUCTO
PLANTILLAS = RAIZ / "platform" / "infra" / "rls-policy-templates"


def _sql(ruta: Path) -> str:
    lineas = [x for x in ruta.read_text(encoding="utf-8").splitlines() if not x.lstrip().startswith("--")]
    return re.sub(r"\s+", " ", " ".join(lineas)).strip().lower()


def test_la_plantilla_es_identica_y_la_de_producto_no_tiene_plantilla():
    assert MIGRACION.read_text(encoding="utf-8") == (PLANTILLAS / NOMBRE).read_text(encoding="utf-8")
    assert not (PLANTILLAS / NOMBRE_PRODUCTO).exists()


def test_el_check_de_status_admite_purging():
    sql = _sql(MIGRACION)
    assert "check (status in ('active', 'suspended', 'inactive', 'purging', 'purged'))" in sql
    assert "check (status <> 'purging' or deactivated_at is not null)" in sql


def test_transiciones_inactive_purging_purged_sin_vuelta_atras():
    sql = _sql(MIGRACION)
    assert "(old.status = 'inactive' and new.status in ('active', 'purging'))" in sql
    assert "(old.status = 'purging' and new.status = 'purged')" in sql
    assert "inactive' and new.status in ('active', 'purged')" not in sql


def test_las_funciones_son_definer_con_search_path_y_execute_solo_para_purge_job():
    for ruta in (MIGRACION, PRODUCTO):
        sql = _sql(ruta)
        assert sql.count("security definer set search_path = ''") >= 1
        for firma in re.findall(r"grant execute on function (public\.\w+\([^)]*\)) to ([^;]+);", sql):
            assert firma[1] == "purge_job", firma
    sql = _sql(MIGRACION)
    for fn in ("begin_tenant_purge(uuid)", "mark_tenant_purged(uuid)", "list_tenants_due_for_purge()"):
        assert f"revoke all on function public.{fn} from public, anon, authenticated, service_role" in sql
        assert f"grant execute on function public.{fn} to purge_job" in sql
    assert (
        "revoke all on function public.purge_tenant_data(uuid) from public, anon, authenticated, service_role"
    ) in _sql(PRODUCTO)


def test_la_gracia_minima_de_30_dias_esta_en_la_base():
    sql = _sql(MIGRACION)
    assert "create trigger platform_settings_validar_gracia before insert or update on public.platform_settings" in sql
    assert ">= 30" in sql and "< 30" in sql


def test_purge_tenant_data_acepta_purging_y_rechaza_el_resto():
    sql = _sql(PRODUCTO)
    assert "perform public.begin_tenant_purge(p_tenant_id)" in sql
    assert "elsif v_status <> 'purging' then" in sql
