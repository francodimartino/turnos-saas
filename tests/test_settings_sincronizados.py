"""Las reglas de `tenants.settings` del dominio coinciden con las que hace cumplir el trigger de la base.

La validación vive en dos lugares (`turnos_dominio.settings_tenant` y la función de trigger `tenants_validar_settings`
de la última migración que la define). Este test lee la migración vigente y compara claves, rangos, formato de color y
forma del logo; si alguien cambia una sola punta, falla. Las reglas se prueban contra la base en el pgTAP de settings.
"""

import re
from pathlib import Path

from turnos_dominio import settings_tenant as dominio

RAIZ = Path(__file__).resolve().parent.parent
MIGRACIONES = RAIZ / "supabase" / "migrations"


def _funcion_vigente() -> str:
    """Cuerpo de `tenants_validar_settings` en la última migración que la redefine."""
    definiciones = [
        m
        for m in sorted(MIGRACIONES.glob("*.sql"))
        if "create or replace function public.tenants_validar_settings" in m.read_text(encoding="utf-8")
    ]
    assert definiciones, "ninguna migración define tenants_validar_settings"
    return definiciones[-1].read_text(encoding="utf-8")


def test_los_rangos_enteros_son_los_de_la_base():
    sql = _funcion_vigente()
    en_base = {
        clave: (int(minimo), int(maximo)) for clave, minimo, maximo in re.findall(r"\('([a-z_]+)', (\d+), (\d+)\)", sql)
    }
    assert en_base == dominio.RANGOS_ENTEROS_SETTINGS


def test_el_plazo_de_cancelacion_tiene_el_rango_y_el_default_decididos():
    assert dominio.RANGOS_ENTEROS_SETTINGS[dominio.CLAVE_PLAZO_CANCELACION] == (0, 720)
    assert dominio.PLAZO_CANCELACION_DEFECTO_HORAS == 24


def test_los_colores_son_las_mismas_claves_y_el_mismo_formato():
    sql = _funcion_vigente()
    claves = re.search(r"array\[([^\]]*)\]", sql)
    assert claves
    assert tuple(re.findall(r"'([a-z_]+)'", claves.group(1))) == dominio.CLAVES_COLOR
    patron_base = re.search(r"!~ '([^']*)'", sql)
    assert patron_base
    # La base ancla con `$` (en Postgres solo coincide al final del texto); el dominio, con el ancla final de Python.
    assert "^" + dominio.PATRON_COLOR.pattern.replace(chr(92) + "Z", "$") == patron_base.group(1)


def test_el_logo_es_la_misma_clave_y_las_mismas_extensiones():
    sql = _funcion_vigente()
    assert f"'{dominio.CLAVE_LOGO}'" in sql
    extensiones = tuple(re.findall(r"/logo[.](\w+)'", sql))
    assert extensiones == dominio.EXTENSIONES_LOGO
    # La base exige el id del propio tenant; el dominio, ese mismo id cuando lo conoce.
    assert "new.id::text" in sql


def test_las_claves_validadas_en_la_base_son_las_conocidas_por_el_dominio():
    sql = _funcion_vigente()
    en_base = set(re.findall(r"\('([a-z_]+)', \d+, \d+\)", sql)) | set(dominio.CLAVES_COLOR) | {dominio.CLAVE_LOGO}
    en_dominio = set(dominio.RANGOS_ENTEROS_SETTINGS) | set(dominio.CLAVES_COLOR) | {dominio.CLAVE_LOGO}
    assert en_base == en_dominio
