"""Test estático: toda query del módulo filtra `tenant_id = $1` de forma explícita (§2.11.4).

platform_admin_readonly bypassea el chequeo de tenant de la RLS al impersonar, así que el filtro
explícito en el SQL es la única barrera real en ese camino. Este test falla si alguna query sobre
una tabla con `tenant_id` (incluidas las tablas unidas con JOIN) no lo trae.
"""

import ast
import inspect
import re

import pytest

from turnos_queries import consultas

# Tablas que no tienen columna tenant_id (el resto de las tablas del esquema sí la tiene).
TABLAS_SIN_TENANT_ID: set[str] = set()

_PALABRAS_NO_ALIAS = {
    "where", "join", "on", "left", "right", "inner", "outer", "cross", "order", "group", "limit", "using",
}
_RE_TABLA = re.compile(r"\b(?:from|join)\s+(?:public\.)?(\w+)(?:\s+(?:as\s+)?(\w+))?", re.IGNORECASE)


def tablas_sin_filtro(sql: str) -> list[str]:
    """Tablas con tenant_id referenciadas en `sql` sin `<tabla|alias>.tenant_id = $1` explícito."""
    tablas = []
    for m in _RE_TABLA.finditer(sql):
        tabla, alias = m.group(1).lower(), (m.group(2) or "").lower()
        if alias in _PALABRAS_NO_ALIAS:
            alias = ""
        tablas.append((tabla, alias))
    faltantes = []
    for tabla, alias in tablas:
        if tabla in TABLAS_SIN_TENANT_ID:
            continue
        nombres = [n for n in (alias, tabla) if n]
        calificado = any(
            re.search(rf"\b{re.escape(n)}\.tenant_id\s*=\s*\$1\b", sql, re.IGNORECASE) for n in nombres
        )
        # Sin calificar solo vale si la query toca una única tabla (no hay ambigüedad posible).
        sin_calificar = len(tablas) == 1 and re.search(r"(?<![.\w])tenant_id\s*=\s*\$1\b", sql, re.IGNORECASE)
        if not (calificado or sin_calificar):
            faltantes.append(tabla)
    return faltantes


# --- el verificador mismo ------------------------------------------------------------------


def test_verificador_detecta_query_sin_filtro():
    assert tablas_sin_filtro("select * from public.bookings where id = $2") == ["bookings"]


def test_verificador_detecta_join_sin_filtro():
    sql = "select * from bookings b join services s on s.id = b.service_id where b.tenant_id = $1"
    assert tablas_sin_filtro(sql) == ["services"]


def test_verificador_no_acepta_filtro_con_otro_parametro():
    assert tablas_sin_filtro("select * from services where tenant_id = $2") == ["services"]


def test_verificador_acepta_query_correcta():
    sql = (
        "select * from public.bookings b join public.services s on s.id = b.service_id "
        "and s.tenant_id = $1 where b.tenant_id = $1"
    )
    assert tablas_sin_filtro(sql) == []
    assert tablas_sin_filtro("select * from services where tenant_id = $1") == []


# --- el módulo -----------------------------------------------------------------------------

CONSTANTES_SQL = {n: v for n, v in vars(consultas).items() if n.startswith("SQL_") and isinstance(v, str)}
FUNCIONES = {
    n: f
    for n, f in vars(consultas).items()
    if inspect.iscoroutinefunction(f) and f.__module__ == consultas.__name__ and not n.startswith("_")
}


def test_el_modulo_expone_consultas():
    assert CONSTANTES_SQL
    assert len(FUNCIONES) >= 7


@pytest.mark.parametrize("nombre", sorted(CONSTANTES_SQL))
def test_toda_query_filtra_por_tenant_id_explicito(nombre):
    assert tablas_sin_filtro(CONSTANTES_SQL[nombre]) == [], f"{nombre}: falta `tenant_id = $1`"


@pytest.mark.parametrize("nombre", sorted(FUNCIONES))
def test_tenant_id_es_obligatorio_y_sin_default(nombre):
    param = inspect.signature(FUNCIONES[nombre]).parameters.get("tenant_id")
    assert param is not None, f"{nombre} no recibe tenant_id"
    assert param.default is inspect.Parameter.empty
    assert param.kind is inspect.Parameter.KEYWORD_ONLY


@pytest.mark.parametrize("nombre", sorted(FUNCIONES))
def test_la_funcion_usa_una_constante_sql_y_no_sql_inline(nombre):
    arbol = ast.parse(inspect.getsource(FUNCIONES[nombre]).lstrip())
    nombres = {n.id for n in ast.walk(arbol) if isinstance(n, ast.Name)}
    assert nombres & set(CONSTANTES_SQL), f"{nombre} no usa ninguna constante SQL_*"
    for n in ast.walk(arbol):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            assert not re.search(r"\b(select|insert|update|delete)\b", n.value, re.IGNORECASE), (
                f"{nombre} tiene SQL inline: mover a una constante SQL_* verificable"
            )


def test_cada_constante_sql_la_usa_alguna_funcion():
    usadas = set()
    for f in FUNCIONES.values():
        usadas |= {n.id for n in ast.walk(ast.parse(inspect.getsource(f).lstrip())) if isinstance(n, ast.Name)}
    assert set(CONSTANTES_SQL) <= usadas
