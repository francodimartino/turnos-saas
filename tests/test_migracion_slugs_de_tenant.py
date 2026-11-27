"""Chequeos estáticos (sin base de datos) de la migración de slugs de tenant.

- La lista de slugs reservados del código y la de la migración son la misma.
- Las funciones son SECURITY DEFINER con search_path vacío y EXECUTE acotado.
- El historial de slugs tiene RLS y ningún grant directo.
"""

import re
from pathlib import Path

from platform_admin.slugs_reservados import SLUGS_RESERVADOS

RAIZ = Path(__file__).resolve().parent.parent
MIGRACION = RAIZ / "supabase" / "migrations" / "20260103003900_slugs_de_tenant.sql"


def _sql() -> str:
    lineas = [x for x in MIGRACION.read_text(encoding="utf-8").splitlines() if not x.lstrip().startswith("--")]
    return re.sub(r"\s+", " ", " ".join(lineas)).strip().lower()


def test_la_lista_de_reservados_del_codigo_es_la_de_la_migracion():
    sql = _sql()
    m = re.search(r"create function public\.slug_reservado.*?array\[(.*?)\]::text\[\]", sql)
    assert m, "no se encontró la lista en slug_reservado"
    assert set(re.findall(r"'([^']+)'", m.group(1))) == set(SLUGS_RESERVADOS)


def test_reservados_incluyen_los_nombres_de_la_landing_y_los_tecnicos():
    esperados = {
        "precios",
        "contacto",
        "privacidad",
        "admin",
        "app",
        "api",
        "login",
        "reservas-api",
        "bot",
        "admin-api",
    }
    assert esperados <= SLUGS_RESERVADOS
    assert all(re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", s) for s in SLUGS_RESERVADOS)


def test_funciones_definer_con_search_path_vacio():
    sql = _sql()
    for nombre in ("tenants_validar_slug", "resolver_slug_tenant", "cambiar_slug_tenant"):
        cabecera = sql[sql.index(f"create function public.{nombre}") :]
        cabecera = cabecera[: cabecera.index("as $$")]
        assert "security definer" in cabecera and "set search_path = ''" in cabecera, nombre


def test_execute_acotado():
    sql = _sql()
    assert "grant execute on function public.cambiar_slug_tenant(uuid, text) to platform_admin;" in sql
    assert "grant execute on function public.resolver_slug_tenant" not in sql
    assert re.findall(r"grant execute on function [^;]*", sql) == [
        "grant execute on function public.cambiar_slug_tenant(uuid, text) to platform_admin"
    ]
    for firma in ("resolver_slug_tenant(text)", "cambiar_slug_tenant(uuid, text)", "slug_reservado(text)"):
        assert f"revoke all on function public.{firma} from public, anon, authenticated, service_role" in sql


def test_historial_con_rls_y_sin_grants_directos():
    sql = _sql()
    assert "alter table public.slugs_anteriores enable row level security" in sql
    assert "create policy tenant_isolation on public.slugs_anteriores" in sql
    assert "revoke all on public.slugs_anteriores from public, anon, authenticated, service_role" in sql
    assert not re.search(r"grant [^;]* on (table )?public\.slugs_anteriores", sql)


def test_ventana_de_redireccion_de_180_dias():
    assert "now() + interval '180 days'" in _sql()


def test_el_slug_solo_cambia_por_funcion_y_la_purga_borra_el_historial():
    sql = _sql()
    assert "create trigger tenants_slug_solo_por_funcion before update of slug on public.tenants" in sql
    assert "current_user = 'platform_admin'" in sql
    assert "create trigger tenants_borrar_slugs_anteriores after update of status on public.tenants" in sql
    assert "delete from public.slugs_anteriores s where s.tenant_id = new.id" in sql
