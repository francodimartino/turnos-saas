"""La lista de roles de login se mantiene a mano en varios lugares: este test falla si alguno difiere de la base.

La fuente es la de las migraciones (los bloques que crean roles de login con `create role ... login`). Se comparan
contra ella: el setup (`scripts/setup_proyecto.py`), los fixtures de integración (`conftest.py`), el entorno local
(`scripts/local.py`), la prueba de arranque (`scripts/verificar_arranque.py`), las variables `SETUP_PASSWORD_<ROL>` de
`docs/deploy.md`, el párrafo de roles de `ARCHITECTURE.md` y las listas de roles de los pgTAP de grants por rol y de
roles sin BYPASSRLS. Los `*_DATABASE_URL` de `render.yaml` no hace falta compararlos: solo nombran los roles de los
servicios que ya están en el Blueprint, y el test verifica que todos existan en la base.
"""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
MIGRACIONES = RAIZ / "supabase" / "migrations"


def _leer(ruta: str) -> str:
    return (RAIZ / ruta).read_text(encoding="utf-8")


def _entre_comillas(texto: str) -> set[str]:
    return set(re.findall(r"'([a-z_]+)'", texto))


def roles_de_la_base() -> set[str]:
    """Roles de login que crean las migraciones (bloques `foreach v_rol in array array[...]` con `create role`)."""
    roles: set[str] = set()
    for ruta in sorted(MIGRACIONES.glob("*.sql")):
        texto = ruta.read_text(encoding="utf-8")
        if "create role %I login" not in texto:
            continue
        for m in re.finditer(r"foreach v_rol in array array\[(.*?)\]\s*loop", texto, re.S):
            roles |= _entre_comillas(m.group(1))
    return roles


def roles_de_una_tupla_python(texto: str, nombre: str) -> set[str]:
    m = re.search(rf"^{nombre} = \((.*?)^\)", texto, re.M | re.S)
    assert m, f"falta la tupla {nombre}"
    return set(re.findall(r'"([a-z_]+)"', m.group(1)))


def roles_de_un_diccionario_python(texto: str, nombre: str) -> set[str]:
    m = re.search(rf"^{nombre} = \{{(.*?)^\}}", texto, re.M | re.S)
    assert m, f"falta el diccionario {nombre}"
    return set(re.findall(r':\s*"([a-z_]+)"', m.group(1)))


def roles_de_deploy(texto: str) -> set[str]:
    m = re.search(r"`SETUP_PASSWORD_<ROL>` \((.*?)\) \|", texto)
    assert m, "docs/deploy.md no lista los SETUP_PASSWORD_<ROL>"
    return {x.lower() for x in re.findall(r"`([A-Z_]+)`", m.group(1))}


def roles_de_arquitectura(texto: str) -> set[str]:
    m = re.search(r"\*\*Cada servicio se conecta con su propio rol de login\*\* \((.*?)\)", texto)
    assert m, "ARCHITECTURE.md no lista los roles de login"
    return set(re.findall(r"`([a-z_]+)`", m.group(1)))


def roles_del_pgtap_de_grants(texto: str) -> set[str]:
    m = re.search(r"from unnest\(array\[(.*?)\]\) as r\(rol\)", texto, re.S)
    assert m, "el pgTAP de grants por rol no lista los roles"
    return _entre_comillas(m.group(1))


def roles_del_pgtap_sin_bypass(texto: str) -> set[str]:
    m = re.search(r"select rolname from pg_roles\s+where rolname in \((.*?)\)\s+and \(rolbypassrls", texto, re.S)
    assert m, "el pgTAP de policies no lista los roles sin BYPASSRLS"
    return _entre_comillas(m.group(1))


def test_la_base_crea_los_ocho_roles_de_login():
    assert roles_de_la_base() == {
        "bot_service",
        "dashboard_service",
        "platform_admin",
        "platform_admin_readonly",
        "purge_job",
        "scheduler_job",
        "web_reservas",
        "enviador",
    }


def test_el_setup_y_los_fixtures_usan_los_roles_de_la_base():
    base = roles_de_la_base()
    assert roles_de_una_tupla_python(_leer("scripts/setup_proyecto.py"), "ROLES_LOGIN") == base
    assert roles_de_una_tupla_python(_leer("conftest.py"), "ROLES_LOGIN") == base


def test_el_entorno_local_y_la_prueba_de_arranque_usan_los_roles_de_la_base():
    base = roles_de_la_base()
    assert roles_de_un_diccionario_python(_leer("scripts/local.py"), "ROLES") == base
    assert roles_de_un_diccionario_python(_leer("scripts/verificar_arranque.py"), "ROLES") == base


def test_la_documentacion_lista_los_roles_de_la_base():
    base = roles_de_la_base()
    assert roles_de_deploy(_leer("docs/deploy.md")) == base
    assert roles_de_arquitectura(_leer("ARCHITECTURE.md")) == base


def test_los_pgtap_de_roles_listan_los_roles_de_la_base():
    base = roles_de_la_base()
    assert roles_del_pgtap_de_grants(_leer("platform/infra/pgtap-tests/31_grants_por_rol.sql")) == base
    assert roles_del_pgtap_sin_bypass(_leer("platform/infra/pgtap-tests/33_policies_plataforma.sql")) == base


def roles_de_las_variables_de_setup(texto: str) -> set[str]:
    return {m.lower() for m in re.findall(r"SETUP_PASSWORD_([A-Z_]+)\s*[:=]", texto)}


def roles_del_pgtap_de_estructura(texto: str) -> set[str]:
    m = re.search(r"select count\(\*\) from pg_roles\s+where rolname in \((.*?)\)\s+and rolcanlogin", texto, re.S)
    assert m, "el pgTAP de estructura no lista los roles de login"
    return _entre_comillas(m.group(1))


def test_las_variables_de_setup_de_compose_y_env_example_son_las_de_la_base():
    base = roles_de_la_base()
    assert roles_de_las_variables_de_setup(_leer("local/docker-compose.yml")) == base
    assert roles_de_las_variables_de_setup(_leer(".env.example")) == base


def test_el_pgtap_de_estructura_lista_los_roles_de_la_base():
    texto = _leer("platform/infra/pgtap-tests/10_core_estructura.sql")
    assert roles_del_pgtap_de_estructura(texto) == roles_de_la_base()


def test_los_roles_de_render_existen_en_la_base():
    usados = set(re.findall(r"_DATABASE_URL # rol ([a-z_]+)", _leer("render.yaml")))
    assert usados and usados <= roles_de_la_base()


def test_la_comparacion_detecta_un_rol_que_falta_o_sobra():
    base = roles_de_la_base()
    sin_uno = "ROLES_LOGIN = (\n" + "".join(f'    "{r}",\n' for r in sorted(base - {"enviador"})) + ")\n"
    assert roles_de_una_tupla_python(sin_uno, "ROLES_LOGIN") == base - {"enviador"} != base
    con_extra = enumerate([*sorted(base), "otro_rol"])
    de_mas = "ROLES = {\n" + "".join(f'    "X_{i}": "{r}",\n' for i, r in con_extra) + "}\n"
    assert roles_de_un_diccionario_python(de_mas, "ROLES") == base | {"otro_rol"} != base
    assert roles_de_deploy("| `SETUP_PASSWORD_<ROL>` (`BOT_SERVICE`, `ENVIADOR`) | x |") == {"bot_service", "enviador"}
