"""Chequeos estáticos (sin base de datos) de la migración de conexión plataforma <-> turnos.

- Es una migración del producto: no tiene plantilla en platform/infra (el template no lleva lógica de producto).
- Ordena después de las migraciones de plataforma y de turnos (§4.2).
- Las funciones nuevas nacen sin EXECUTE (revoke explícito) y fijan search_path; los reemplazos
  (create or replace) conservan la firma de las que reemplazan.
- Los grants de calendar_scrub_targets son exactamente los de §3.2.
"""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
NOMBRE = "20260103000100_conexion.sql"
MIGRACIONES = RAIZ / "supabase" / "migrations"

FUNCIONES = {
    "payments_forzar_tenant_id",
    "anonymize_end_customer_core",
    "purge_tenant_data",
    "calendar_scrub_targets_core",
    "calendar_scrub_targets",
}
# Funciones con EXECUTE para algún rol de aplicación -> roles
GRANTS = {
    "purge_tenant_data": {"purge_job"},
    "calendar_scrub_targets": {"bot_service", "dashboard_service", "platform_admin", "purge_job"},
}
# Funciones que se reemplazan: la firma tiene que ser idéntica a la original
REEMPLAZOS = {
    "anonymize_end_customer_core": "p_id uuid, p_actor_type text, p_actor_id uuid",
    "purge_tenant_data": "p_tenant_id uuid",
    "payments_forzar_tenant_id": "",
}


def _sql() -> str:
    return (MIGRACIONES / NOMBRE).read_text(encoding="utf-8")


def test_la_migracion_existe_y_ordena_despues_de_turnos():
    assert (MIGRACIONES / NOMBRE).is_file()
    previas = sorted(p.name for p in MIGRACIONES.glob("*.sql") if p.name < NOMBRE)
    assert any(n.startswith("20260102") for n in previas)


def test_no_tiene_plantilla_de_plataforma():
    assert not (RAIZ / "platform" / "infra" / "rls-policy-templates" / NOMBRE).exists()


def test_define_exactamente_las_funciones_esperadas():
    nombres = set(re.findall(r"create (?:or replace )?function public\.(\w+)\(", _sql(), re.IGNORECASE))
    assert nombres == FUNCIONES


def test_las_funciones_existentes_se_reemplazan_con_create_or_replace():
    sql = _sql()
    for nombre in REEMPLAZOS:
        assert re.search(rf"create or replace function public\.{nombre}\(", sql, re.IGNORECASE), nombre
    for nombre in FUNCIONES - set(REEMPLAZOS):
        assert re.search(rf"create function public\.{nombre}\(", sql, re.IGNORECASE), nombre


def test_los_reemplazos_conservan_la_firma_original():
    sql = _sql()
    for nombre, firma in REEMPLAZOS.items():
        m = re.search(
            rf"create or replace function public\.{nombre}\((.*?)\)\s*returns", sql, re.DOTALL | re.IGNORECASE
        )
        assert m, nombre
        encontrada = " ".join(m.group(1).split())
        assert encontrada == firma, f"{nombre}: la firma cambió ({encontrada!r})"


def test_las_funciones_de_aplicacion_revocan_execute_y_fijan_search_path():
    sql = _sql()
    for nombre in FUNCIONES - {"payments_forzar_tenant_id"}:
        assert re.search(
            rf"revoke all on function public\.{nombre}\(.*?\)\s+from public, anon, authenticated, service_role",
            sql,
            re.DOTALL | re.IGNORECASE,
        ), f"{nombre}: falta revocar EXECUTE de PUBLIC/anon/authenticated/service_role"
    for cuerpo in re.split(r"create (?:or replace )?function ", sql, flags=re.IGNORECASE)[1:]:
        encabezado = cuerpo.split("$$", 1)[0]
        assert "set search_path = ''" in encabezado, cuerpo[:60]


def test_los_grants_son_exactamente_los_de_la_arquitectura():
    sql = _sql()
    otorgados: dict[str, set[str]] = {}
    for nombre, roles in re.findall(r"grant execute on function public\.(\w+)\(.*?\)\s+to ([\w, ]+);", sql, re.DOTALL):
        otorgados.setdefault(nombre, set()).update(r.strip() for r in roles.split(","))
    assert otorgados == GRANTS


def test_las_core_no_se_otorgan_a_nadie_y_corren_como_invocador():
    sql = _sql()
    assert "grant execute on function public.anonymize_end_customer_core" not in sql
    assert "grant execute on function public.calendar_scrub_targets_core" not in sql
    for nombre in ("anonymize_end_customer_core", "calendar_scrub_targets_core"):
        encabezado = re.split(rf"create (?:or replace )?function public\.{nombre}\(", sql, flags=re.IGNORECASE)[1]
        assert "security definer" not in encabezado.split("$$", 1)[0].lower(), nombre


def test_la_anonimizacion_audita_el_payer_y_la_purga_anonimiza_professionals():
    sql = _sql()
    assert "'payments.payer'" in sql
    assert re.search(r"update public\.professionals", sql, re.IGNORECASE)
    assert re.search(r"update public\.payments", sql, re.IGNORECASE)
    assert re.search(r"update public\.bookings", sql, re.IGNORECASE)


def test_no_cita_el_kit_ni_incluye_secretos():
    sql = _sql()
    for prohibido in ("ARNES", "arnes", "password", "secret_key", "api_key"):
        assert prohibido not in sql, prohibido
