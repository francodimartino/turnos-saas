"""Chequeos estáticos (sin base de datos) de la migración de anonimización y purga de la web de reservas.

- Otorga exactamente dos privilegios: web_reservas inserta `codigos_web.end_customer_id` y purge_job ejecuta
  `purge_tenant_data` (recreada con otro tipo de retorno, así que hay que volver a otorgarlo).
- La función interna de anonimización sigue sin EXECUTE para ningún rol (recibe el actor como parámetro).
- La anonimización toca todo lo que dice el contrato (email, política, canales, sesiones, códigos) y la purga todas
  las tablas de la web, los avisos al negocio y los slugs anteriores.
El comportamiento contra la base real está en `supabase/tests/turnos_80_*` y en
`tests/integracion/test_anonimizacion_purga.py`.
"""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
MIGRACION = RAIZ / "supabase" / "migrations" / "20260103004480_anonimizacion_y_purga_web.sql"


def _sql() -> str:
    lineas = [x for x in MIGRACION.read_text(encoding="utf-8").splitlines() if not x.lstrip().startswith("--")]
    return re.sub(r"\s+", " ", " ".join(lineas)).strip().lower()


def _funcion(nombre: str) -> str:
    m = re.search(rf"create (?:or replace )?function public\.{nombre}\(.*?\$\$;", _sql(), re.S)
    assert m, nombre
    return m.group(0)


def test_los_unicos_grants_son_los_dos_esperados():
    assert re.findall(r"grant [^;]+;", _sql()) == [
        "grant insert (end_customer_id) on public.codigos_web to web_reservas;",
        "grant execute on function public.purge_tenant_data(uuid) to purge_job;",
    ]


def test_las_funciones_nuevas_no_se_ejecutan_por_public_ni_los_roles_de_supabase():
    sql = _sql()
    assert (
        "revoke all on function public.purge_tenant_data(uuid) from public, anon, authenticated, service_role;" in sql
    )
    # la interna se redefine con create or replace: conserva sus grants (ninguno) y no se otorga acá
    assert "anonymize_end_customer_core" not in " ".join(re.findall(r"grant [^;]+;", sql))


def test_la_purga_se_recrea_porque_cambia_el_tipo_de_retorno():
    sql = _sql()
    assert sql.index("drop function public.purge_tenant_data(uuid);") < sql.index(
        "create function public.purge_tenant_data(p_tenant_id uuid)"
    )
    assert "returns table (auth_user_id uuid, logo_ruta text)" in _funcion("purge_tenant_data")
    assert "security definer set search_path = ''" in _funcion("purge_tenant_data")


def test_la_anonimizacion_borra_email_politica_canales_sesiones_y_codigos():
    cuerpo = _funcion("anonymize_end_customer_core")
    for fragmento in (
        "email = null",
        "politica_version = null",
        "politica_aceptada_at = null",
        "delete from public.canales_cliente",
        "delete from public.sesiones_web",
        "delete from public.codigos_web",
        "'web'",
    ):
        assert fragmento in cuerpo, fragmento


def test_la_purga_borra_todo_lo_que_dice_el_contrato_y_devuelve_el_logo():
    cuerpo = _funcion("purge_tenant_data")
    for tabla in ("sesiones_web", "codigos_web", "canales_cliente", "avisos_negocio_config", "slugs_anteriores"):
        assert re.search(rf"delete from public\.{tabla} \w+ where \w+\.tenant_id = p_tenant_id", cuerpo), tabla
    assert "settings ->> 'logo_ruta'" in cuerpo
    # no borra logo_ruta de settings: un reintento tiene que volver a recibirla
    assert "update public.tenants" not in cuerpo


def test_el_check_de_audit_log_y_el_mapeo_del_rol_incluyen_el_actor_web():
    sql = _sql()
    assert "check (actor_type in ('staff', 'bot', 'web', 'system', 'platform_admin'))" in sql
    assert "when 'web_reservas' then 'web'" in sql


def test_la_migracion_no_cita_planes_tareas_ni_olas():
    texto = MIGRACION.read_text(encoding="utf-8").lower()
    for prohibido in ("tarea", "ola ", "plan ", "arnes", "arnés"):
        assert prohibido not in texto, prohibido
