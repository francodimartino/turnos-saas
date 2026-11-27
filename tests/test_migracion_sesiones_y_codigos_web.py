"""Chequeos estáticos de la migración de sesiones y códigos de la web.

- Ninguna columna de las dos tablas guarda un token, un código, un email o una IP en claro.
- Los límites (30 días, 10 minutos, 5 intentos) y el formato del hash están en la base.
- Hay un test pgTAP que intenta violar cada uno.
"""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
MIGRACION = RAIZ / "supabase" / "migrations" / "20260103004350_sesiones_y_codigos_web.sql"
PRUEBA_PGTAP = RAIZ / "supabase" / "tests" / "turnos_73_sesiones_y_codigos_web.sql"


def _sql(ruta: Path) -> str:
    sin_comentarios = "\n".join(x.split("--")[0] for x in ruta.read_text(encoding="utf-8").splitlines())
    return re.sub(r"\s+", " ", sin_comentarios.lower())


def _columnas(tabla: str) -> dict[str, str]:
    m = re.search(rf"create table public\.{tabla} \((.*?)\n?\);", _sql(MIGRACION).replace(") ;", ");"))
    assert m, f"no se encontró la tabla {tabla}"
    cuerpo = m.group(1)
    columnas: dict[str, str] = {}
    for definicion in re.split(r",(?![^(]*\))", cuerpo):
        partes = definicion.strip().split(" ", 1)
        if partes[0] in {"check", "constraint", "foreign", "unique", "primary"}:
            continue
        columnas[partes[0]] = partes[1] if len(partes) > 1 else ""
    return columnas


def test_ninguna_columna_guarda_el_valor_en_claro():
    for tabla in ("sesiones_web", "codigos_web"):
        for nombre, definicion in _columnas(tabla).items():
            if definicion.startswith(("text", "varchar", "bytea")):
                assert nombre.endswith("_hash"), f"{tabla}.{nombre} guarda texto que no es un hash"
                assert "[0-9a-f]{64}" in definicion, f"{tabla}.{nombre} no fija el formato del hash"
    todas = set(_columnas("sesiones_web")) | set(_columnas("codigos_web"))
    assert not {"token", "codigo", "email", "ip"} & todas


def test_los_limites_estan_en_la_base():
    sql = _sql(MIGRACION)
    assert "intentos smallint not null default 0 check (intentos between 0 and 5)" in sql
    assert "expira_at <= created_at + interval '10 minutes'" in sql
    assert "expira_at <= created_at + interval '30 days'" in sql
    assert "create unique index sesiones_web_token_hash_uidx on public.sesiones_web (token_hash)" in sql


def test_ambas_tablas_tienen_rls_y_ningun_grant():
    sql = _sql(MIGRACION)
    for tabla in ("sesiones_web", "codigos_web"):
        assert f"alter table public.{tabla} enable row level security" in sql
        assert f"create policy tenant_isolation on public.{tabla}" in sql
    assert not re.search(r"grant [^;]* to (bot_service|dashboard_service|platform_admin)", sql)


def test_la_prueba_pgtap_viola_cada_limite():
    sql = _sql(PRUEBA_PGTAP)
    for fragmento in (
        "'2026-02-04 10:00:01+00'",  # sesión de 30 días y un segundo
        "'2026-01-05 10:10:01+00'",  # código de 10 minutos y un segundo
        "intentos = intentos + 1",  # de 5 a 6 intentos
        "repeat('a', 63)",
        "repeat('a', 65)",
    ):
        assert fragmento in sql, fragmento


def test_los_codigos_vencidos_se_podan_en_la_base():
    sql = _sql(MIGRACION)
    assert "create trigger podar_codigos_web_al_insertar after insert on public.codigos_web" in sql
    assert "create function public.podar_codigos_web_vencidos()" in sql
    assert "revoke all on function public.podar_codigos_web_vencidos() from public" in sql
    assert not re.search(r"grant execute on function public\.podar_codigos_web", sql)
    assert "c.created_at < now() - interval '1 hour'" in sql
    assert "c.expira_at < now()" not in sql
