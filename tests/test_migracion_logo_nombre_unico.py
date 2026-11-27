"""La migración del nombre único del logo limpia las filas con la ruta anterior antes de endurecer el trigger."""

from pathlib import Path

MIGRACION = Path(__file__).resolve().parent.parent / "supabase" / "migrations" / "20260103004450_logo_nombre_unico.sql"


def test_quita_logo_ruta_con_formato_viejo_antes_de_reemplazar_la_funcion():
    sql = MIGRACION.read_text(encoding="utf-8")
    actualizacion = sql.index("update public.tenants")
    funcion = sql.index("create or replace function public.tenants_validar_settings")
    assert actualizacion < funcion  # con la regla anterior todavía vigente
    bloque = sql[actualizacion:funcion]
    assert "settings - 'logo_ruta'" in bloque
    assert "!~" in bloque and "logo-[0-9a-f]{32}" in bloque  # solo toca lo que el trigger nuevo rechazaría
