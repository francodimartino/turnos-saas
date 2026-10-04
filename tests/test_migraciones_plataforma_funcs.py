"""Chequeos estáticos (sin base de datos) de la migración de plataforma 4.

- supabase/config.toml registra custom_access_token_hook como Auth Hook.
- La migración de supabase/ es idéntica a su plantilla en platform/infra/rls-policy-templates/.
- Ninguna función nueva queda con EXECUTE por defecto: todas revocan de PUBLIC/anon/authenticated/service_role.
"""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
NOMBRE = "20260101000400_platform_funcs_a.sql"


def _seccion_toml(texto: str, nombre: str) -> str | None:
    coincidencia = re.search(rf"^\[{re.escape(nombre)}\]\s*$(.*?)(?=^\[|\Z)", texto, re.MULTILINE | re.DOTALL)
    return coincidencia.group(1) if coincidencia else None


def test_config_toml_registra_el_auth_hook():
    texto = (RAIZ / "supabase" / "config.toml").read_text(encoding="utf-8")
    seccion = _seccion_toml(texto, "auth.hook.custom_access_token")
    assert seccion is not None, "falta [auth.hook.custom_access_token] en supabase/config.toml"
    sin_comentarios = "\n".join(linea for linea in seccion.splitlines() if not linea.lstrip().startswith("#"))
    assert re.search(r"^enabled\s*=\s*true\s*$", sin_comentarios, re.MULTILINE)
    assert re.search(
        r'^uri\s*=\s*"pg-functions://postgres/public/custom_access_token_hook"\s*$', sin_comentarios, re.MULTILINE
    )


def test_la_migracion_es_identica_a_su_plantilla():
    plantilla = RAIZ / "platform" / "infra" / "rls-policy-templates" / NOMBRE
    migracion = RAIZ / "supabase" / "migrations" / NOMBRE
    assert plantilla.is_file(), f"falta la plantilla {plantilla}"
    assert migracion.is_file(), f"falta la migración {migracion}"
    assert plantilla.read_bytes() == migracion.read_bytes()


def test_toda_funcion_definer_fija_search_path_y_revoca_execute():
    sql = (RAIZ / "supabase" / "migrations" / NOMBRE).read_text(encoding="utf-8")
    definiciones = re.findall(
        r"create (?:or replace )?function public\.(\w+)\((.*?)\)\s*returns.*?\$\$;",
        sql,
        re.DOTALL | re.IGNORECASE,
    )
    assert len(definiciones) >= 12
    for nombre, _args in definiciones:
        assert re.search(
            rf"revoke all on function public\.{nombre}\(.*?\)\s+from public, anon, authenticated, service_role",
            sql,
            re.DOTALL | re.IGNORECASE,
        ), f"{nombre}: falta revocar EXECUTE de PUBLIC/anon/authenticated/service_role"
    cuerpos = re.split(r"create (?:or replace )?function ", sql, flags=re.IGNORECASE)[1:]
    for cuerpo in cuerpos:
        encabezado = cuerpo.split("$$", 1)[0]
        assert re.search(r"set search_path\s*=", encabezado, re.IGNORECASE), cuerpo[:60]
        if "security definer" in encabezado.lower():
            assert "search_path = ''" in encabezado or "search_path = public" in encabezado
