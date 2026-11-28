"""Los tipos de actor de `audit_log.actor_type` se enumeran a mano en varios lugares: este test falla si alguno difiere.

La fuente es el último `check (actor_type in (...))` de las migraciones. Se comparan contra ella: la función
`audit_actor_type` (qué rol de login produce cada tipo), la validación del visor de auditoría de `platform_admin`, los
filtros de las dos copias de la pantalla de auditoría del panel admin (la del producto y la de la plantilla), el
comentario de la tabla en `ARCHITECTURE.md` y la lista de actores que acepta `anonymize_end_customer_core`.
"""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
MIGRACIONES = RAIZ / "supabase" / "migrations"


def _comillas(texto: str) -> set[str]:
    return set(re.findall(r"'([a-z_]+)'", texto))


def _migraciones() -> list[Path]:
    return sorted(MIGRACIONES.glob("*.sql"))


def actores_de_la_base() -> set[str]:
    """Valores del último check de `audit_log.actor_type` que declara alguna migración."""
    ultimo: set[str] | None = None
    for ruta in _migraciones():
        texto = ruta.read_text(encoding="utf-8")
        for m in re.finditer(r"actor_type\s+(?:text not null )?check \(actor_type in \((.*?)\)\)", texto, re.S):
            ultimo = _comillas(m.group(1))
        for m in re.finditer(r"constraint audit_log_actor_type_check\s+check \(actor_type in \((.*?)\)\)", texto, re.S):
            ultimo = _comillas(m.group(1))
    assert ultimo, "ninguna migración declara el check de audit_log.actor_type"
    return ultimo


def _ultima_funcion(patron_nombre: str) -> str:
    """Texto de la última definición (`create [or replace] function <nombre>`) entre las migraciones."""
    cuerpo = ""
    for ruta in _migraciones():
        texto = ruta.read_text(encoding="utf-8")
        for m in re.finditer(
            rf"create (?:or replace )?function {patron_nombre}\(.*?^\$\$;?$", texto, re.S | re.M | re.I
        ):
            cuerpo = m.group(0)
    assert cuerpo, patron_nombre
    return cuerpo


def test_el_check_de_la_base_tiene_los_cinco_actores():
    assert actores_de_la_base() == {"staff", "bot", "web", "system", "platform_admin"}


def test_audit_actor_type_solo_produce_actores_validos_y_uno_por_rol_de_aplicacion():
    cuerpo = _ultima_funcion(r"public\.audit_actor_type")
    pares = re.findall(r"when '([a-z_]+)' then '([a-z_]+)'", cuerpo)
    assert {actor for _rol, actor in pares} <= actores_de_la_base()
    assert dict(pares) == {
        "bot_service": "bot",
        "dashboard_service": "staff",
        "web_reservas": "web",
        "platform_admin": "platform_admin",
        "purge_job": "system",
    }


def test_anonymize_end_customer_core_acepta_exactamente_los_actores_de_la_base():
    cuerpo = _ultima_funcion(r"public\.anonymize_end_customer_core")
    m = re.search(r"p_actor_type not in \((.*?)\)", cuerpo, re.S)
    assert m
    assert _comillas(m.group(1)) == actores_de_la_base()


def test_el_visor_de_auditoria_valida_los_mismos_actores():
    texto = (RAIZ / "platform/packages/platform_admin/platform_admin/auditoria.py").read_text(encoding="utf-8")
    m = re.search(r"_ACTORES = frozenset\(\{(.*?)\}\)", texto, re.S)
    assert m
    assert set(re.findall(r'"([a-z_]+)"', m.group(1))) == actores_de_la_base()


def test_las_pantallas_de_auditoria_filtran_por_los_mismos_actores():
    for ruta in (
        "apps/admin-frontend/src/paginas/Auditoria.tsx",
        "platform/admin-frontend-template/src/paginas/Auditoria.tsx",
    ):
        texto = (RAIZ / ruta).read_text(encoding="utf-8")
        valores = set(re.findall(r'<option value="([a-z_]+)">', texto))
        assert valores == actores_de_la_base(), ruta


def test_architecture_documenta_los_mismos_actores():
    texto = (RAIZ / "ARCHITECTURE.md").read_text(encoding="utf-8")
    m = re.search(r"actor_type text not null,\s+-- ([^\n]*)", texto)
    assert m
    assert _comillas(m.group(1)) == actores_de_la_base()
