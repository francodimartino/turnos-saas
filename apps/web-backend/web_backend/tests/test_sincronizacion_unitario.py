"""Listas que se mantienen a mano y la documentación del servicio, cubiertas por test."""

import ast
import re
from pathlib import Path

from web_backend import consultas, huecos, negocio

PAQUETE = Path(__file__).resolve().parent.parent
README = (PAQUETE.parent / "README.md").read_text(encoding="utf-8")
# Códigos que emiten los middlewares y paquetes compartidos que el servicio monta: no están en este paquete.
CODIGOS_COMPARTIDOS = {"cuerpo_demasiado_grande", "error_interno", "servicio_ocupado"}


def _codigos_del_readme() -> set[str]:
    tabla = README.split("## Errores")[1].split("## Tests")[0]
    filas = tabla.split("| Estado | Código | Cuándo |")[1]
    return set(re.findall(r"`([a-z_]+)`", filas))


def _fuentes() -> str:
    return "\n".join(r.read_text(encoding="utf-8") for r in PAQUETE.glob("*.py"))


def _codigos_emitidos_por_el_codigo() -> set[str]:
    """Códigos literales que arma el paquete con `error_http` y `cuerpo_error`, y los de las clases de `huecos`."""
    codigos: set[str] = set()
    for ruta in PAQUETE.glob("*.py"):
        for nodo in ast.walk(ast.parse(ruta.read_text(encoding="utf-8"))):
            if isinstance(nodo, ast.Call):
                nombre = getattr(nodo.func, "id", "")
                args = nodo.args
                if nombre == "error_http" and len(args) > 1 and isinstance(args[1], ast.Constant):
                    codigos.add(args[1].value)
                if nombre == "cuerpo_error" and args and isinstance(args[0], ast.Constant):
                    codigos.add(args[0].value)
    clases = [c for c in vars(huecos).values() if isinstance(c, type) and issubclass(c, huecos.ErrorHuecos)]
    codigos |= {c.codigo for c in clases}
    return {c for c in codigos if isinstance(c, str)}


def test_el_readme_documenta_cada_codigo_de_error_que_el_servicio_arma():
    faltan = _codigos_emitidos_por_el_codigo() - {"pedido_invalido"} - _codigos_del_readme()
    assert not faltan, f"códigos que el servicio emite y el README no documenta: {sorted(faltan)}"


def test_cada_codigo_del_readme_existe_en_el_codigo():
    fuentes = _fuentes()
    for codigo in _codigos_del_readme():
        assert f'"{codigo}"' in fuentes or codigo in CODIGOS_COMPARTIDOS, f"el README documenta {codigo!r} y no existe"


def test_el_readme_nombra_las_rutas_que_existen():
    from web_backend.rutas_negocio import router

    rutas = {r.path for r in router.routes}
    assert rutas == {"/negocios/{slug}", "/negocios/{slug}/huecos"}
    for ruta in rutas:
        assert f"GET {ruta}" in README


def test_las_claves_de_settings_leidas_por_el_sql_son_las_publicas_mas_la_anticipacion():
    sql = "\n".join([consultas.SQL_NEGOCIO, consultas.SQL_AVISO_MINIMO])
    leidas = set(re.findall(r"settings\s*->>?\s*'(\w+)'", sql))
    assert leidas == set(negocio.CLAVES_PUBLICAS_DE_SETTINGS) | {"booking_min_notice_minutes"}
    for otra in (consultas.SQL_CATALOGO, consultas.SQL_PAR_RESERVABLE, consultas.SQL_RESOLVER_SLUG):
        assert "settings" not in otra


def test_ninguna_consulta_pide_columnas_que_el_rol_de_la_web_no_puede_leer():
    # Grants de `web_reservas` sobre `tenants`: solo id, name y settings.
    for sql in (consultas.SQL_NEGOCIO, consultas.SQL_AVISO_MINIMO):
        assert re.findall(r"\b(?:plan|status|paid_until|billing_notes|deactivated_at|slug)\b", sql) == []
    todas = " ".join([consultas.SQL_NEGOCIO, consultas.SQL_CATALOGO])
    assert not re.search(r"select\s+\*", todas, re.IGNORECASE)


def test_el_paquete_no_cita_documentos_de_gestion_del_proyecto():
    prohibidos = re.compile(r"arn[eé]s|\bola\s*\d|\btarea\s*\d|\bplan\s+web", re.IGNORECASE)
    for ruta in [*PAQUETE.rglob("*.py"), PAQUETE.parent / "README.md", PAQUETE.parent / ".env.example"]:
        for numero, linea in enumerate(ruta.read_text(encoding="utf-8").splitlines(), 1):
            assert not prohibidos.search(linea), f"{ruta.name}:{numero}: {linea.strip()}"
