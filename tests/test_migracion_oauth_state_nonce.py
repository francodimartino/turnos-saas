"""Chequeos estáticos (sin base de datos) de la migración del nonce de un solo uso del state de OAuth.

- La tabla tiene RLS, policy por tenant y clave primaria (tenant_id, nonce).
- La función es SECURITY DEFINER con search_path vacío y EXECUTE solo para dashboard_service.
- La plantilla de plataforma es idéntica a la migración.
"""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
NOMBRE = "20260103001400_oauth_state_nonce.sql"
MIGRACION = RAIZ / "supabase" / "migrations" / NOMBRE
PLANTILLA = RAIZ / "platform" / "infra" / "rls-policy-templates" / NOMBRE


def _sql(ruta: Path) -> str:
    lineas = [x for x in ruta.read_text(encoding="utf-8").splitlines() if not x.lstrip().startswith("--")]
    return re.sub(r"\s+", " ", " ".join(lineas)).strip().lower()


def test_la_tabla_tiene_rls_policy_y_clave_por_tenant():
    sql = _sql(MIGRACION)
    assert "alter table public.oauth_state_nonces enable row level security" in sql
    assert "create policy tenant_isolation on public.oauth_state_nonces" in sql
    assert "primary key (tenant_id, nonce)" in sql


def test_la_funcion_es_definer_con_search_path_y_execute_solo_para_dashboard():
    sql = _sql(MIGRACION)
    assert "security definer set search_path = ''" in sql
    assert (
        "revoke all on function public.consume_oauth_state_nonce(text, timestamptz) "
        "from public, anon, authenticated, service_role"
    ) in sql
    patron = r"grant execute on function public\.consume_oauth_state_nonce\(text, timestamptz\) to ([^;]+)"
    grants = re.findall(patron, sql)
    assert grants == ["dashboard_service"]


def test_ningun_grant_directo_sobre_la_tabla():
    assert not re.search(r"grant [^;]* on (table )?public\.oauth_state_nonces", _sql(MIGRACION))


def test_la_plantilla_es_identica_a_la_migracion():
    assert MIGRACION.read_text(encoding="utf-8") == PLANTILLA.read_text(encoding="utf-8")
