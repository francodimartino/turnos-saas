"""Listas que se mantienen a mano en más de un lugar: cada una tiene un test que falla si difieren.

Cubiertas aquí: raíces de paquete de pyproject.toml, FRONTENDS (verificar.py / CI / dependabot), plantillas SQL de
`platform/infra/rls-policy-templates` contra `supabase/migrations`, campos de PII (Python / Sentry de los frontends)
y slugs reservados (base / código Python / web-frontend).
Cubiertas en otros tests: CAMPOS_EXTRA_PERMITIDOS, paquetes del pip install, módulos del import-check, allowlists
pgTAP, archivos .py de mypy `files`, contratos y permisos.
"""

import importlib.util
import re
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent


def _leer(ruta: str) -> str:
    return (RAIZ / ruta).read_text(encoding="utf-8")


# --- Raíces de paquete de pyproject.toml ---------------------------------------------------------------------------


def _lista_toml(texto: str, seccion: str, clave: str) -> list[str]:
    """Valores de `clave = [ ... ]` dentro de `[seccion]` (sin tomllib: el Python local puede ser < 3.11)."""
    m = re.search(rf"^\[{re.escape(seccion)}\]\n(.*?)(?=^\[|\Z)", texto, re.M | re.S)
    assert m, f"falta la sección [{seccion}]"
    c = re.search(rf"^{clave}\s*=\s*\[(.*?)\]", m.group(1), re.M | re.S)
    assert c, f"falta {clave} en [{seccion}]"
    sin_comentarios = re.sub(r"#[^\n]*", "", c.group(1))
    return re.findall(r'"([^"]+)"', sin_comentarios)


def _listas_de_paquetes(texto: str) -> dict[str, list[str]]:
    return {
        "packages.find.where": _lista_toml(texto, "tool.setuptools.packages.find", "where"),
        "pytest pythonpath": _lista_toml(texto, "tool.pytest.ini_options", "pythonpath"),
        "mypy mypy_path": _lista_toml(texto, "tool.mypy", "mypy_path"),
    }


def _raices_reales(raiz: Path) -> set[str]:
    """Carpetas de apps/* y platform/packages/* que contienen al menos un paquete Python (con __init__.py)."""
    candidatas = [*(raiz / "apps").glob("*"), *(raiz / "platform" / "packages").glob("*")]
    return {
        c.relative_to(raiz).as_posix()
        for c in candidatas
        if c.is_dir()
        and "node_modules" not in c.parts
        and any(h.is_dir() and (h / "__init__.py").exists() for h in c.iterdir())
    }


def _diferencias_de_paquetes(texto: str, raiz: Path) -> list[str]:
    errores = []
    listas = _listas_de_paquetes(texto)
    base_nombre, base = next(iter(listas.items()))
    for nombre, lista in listas.items():
        if len(lista) != len(set(lista)):
            errores.append(f"{nombre} tiene entradas repetidas")
        if set(lista) != set(base):
            errores.append(f"{nombre} difiere de {base_nombre}: {sorted(set(lista) ^ set(base))}")
    reales = _raices_reales(raiz)
    for nombre, lista in listas.items():
        if sin_listar := sorted(reales - set(lista)):
            errores.append(f"{nombre}: raíces de paquete sin listar {sin_listar}")
        if inexistentes := sorted(r for r in lista if r not in reales):
            errores.append(f"{nombre}: listadas que no son raíz de paquete {inexistentes}")
    return errores


def test_listas_de_paquetes_de_pyproject_coinciden_entre_si_y_con_el_arbol():
    assert _diferencias_de_paquetes(_leer("pyproject.toml"), RAIZ) == []


def test_listas_de_paquetes_detecta_diferencia_entre_listas():
    texto = _leer("pyproject.toml")
    roto = texto.replace('    "apps/jobs",\n]\nasyncio_mode', "]\nasyncio_mode")
    assert roto != texto
    assert any("pytest pythonpath difiere" in e for e in _diferencias_de_paquetes(roto, RAIZ))


def test_listas_de_paquetes_detecta_raiz_sin_listar_y_listada_inexistente(tmp_path):
    texto = _leer("pyproject.toml")
    # Árbol falso con una raíz real más que las listadas, y sin una de las listadas.
    reales = (_raices_reales(RAIZ) - {"apps/jobs"}) | {"apps/nueva"}
    for r in reales:
        (tmp_path / r / "paquete").mkdir(parents=True)
        (tmp_path / r / "paquete" / "__init__.py").write_text("")
    errores = _diferencias_de_paquetes(texto, tmp_path)
    assert any("sin listar ['apps/nueva']" in e for e in errores)
    assert any("que no son raíz de paquete ['apps/jobs']" in e for e in errores)


# --- FRONTENDS: verificar.py / matriz del CI / dependabot -----------------------------------------------------------


def _frontends_verificar() -> set[str]:
    spec = importlib.util.spec_from_file_location("verificar", RAIZ / "scripts" / "verificar.py")
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return set(modulo.FRONTENDS)


def _frontends_ci(texto_ci: str) -> set[str]:
    """Rutas de la matriz de frontends del CI: `apps/<app>`, o `platform/<app>` si la expresión del
    `working-directory` lo dice (`matrix.app == '<app>' && 'platform' || 'apps'`)."""
    job = yaml.safe_load(texto_ci)["jobs"]["frontends"]
    paso = next(p for p in job["steps"] if "working-directory" in p)
    en_platform = set(re.findall(r"matrix\.app == '([^']+)' && 'platform'", paso["working-directory"]))
    return {f"{'platform' if app in en_platform else 'apps'}/{app}" for app in job["strategy"]["matrix"]["app"]}


def _frontends_dependabot(texto: str) -> set[str]:
    return {u["directory"].strip("/") for u in yaml.safe_load(texto)["updates"] if u["package-ecosystem"] == "npm"}


def test_frontends_coinciden_en_verificar_ci_y_dependabot():
    esperados = _frontends_verificar()
    assert _frontends_ci(_leer(".github/workflows/ci.yml")) == esperados
    assert _frontends_dependabot(_leer(".github/dependabot.yml")) == esperados


def test_frontends_existen_en_disco():
    for ruta in _frontends_verificar():
        assert (RAIZ / ruta / "package.json").exists(), ruta


def test_frontends_detecta_un_frontend_que_falta_en_dependabot():
    texto = _leer(".github/dependabot.yml")
    roto = re.sub(r'  - package-ecosystem: npm\n    directory: "/apps/admin-frontend"\n(?:    [^\n]*\n)+', "", texto)
    assert roto != texto
    assert _frontends_dependabot(roto) != _frontends_verificar()


def test_frontends_detecta_un_frontend_que_falta_en_la_matriz_del_ci():
    ci = _leer(".github/workflows/ci.yml")
    roto = ci.replace(
        "app: [dashboard-frontend, admin-frontend, web-frontend]", "app: [dashboard-frontend, admin-frontend]"
    )
    assert roto != ci
    assert _frontends_ci(roto) != _frontends_verificar()


# --- Checks requeridos de los frontends: matriz del CI vs AGENTS.md y ARCHITECTURE.md -------------------------


def test_los_checks_de_cada_frontend_figuran_en_la_lista_de_checks_requeridos():
    job = yaml.safe_load(_leer(".github/workflows/ci.yml"))["jobs"]["frontends"]
    for app in job["strategy"]["matrix"]["app"]:
        check = f"`{job['name']} ({app})`"
        for doc in ("AGENTS.md", "ARCHITECTURE.md"):
            assert check in _leer(doc), f"{doc} no lista el check {check}"


# --- Plantillas SQL vs migraciones -------------------------------------------------------------------------------


def _lineas_sql(texto: str) -> list[str]:
    """Líneas con contenido (sin comentarios `--`, sin vacías, con espacios normalizados)."""
    salida = []
    for linea in texto.splitlines():
        limpia = re.sub(r"\s+", " ", linea.split("--", 1)[0]).strip()
        if limpia:
            salida.append(limpia)
    return salida


def _plantilla_contenida_en(plantilla: str, migracion: str) -> bool:
    """Equivalente: todo el contenido de la plantilla está, en orden, en la migración (que puede sumar lo de
    turnos, ya que la plantilla solo trae la parte de plataforma)."""
    resto = iter(_lineas_sql(migracion))
    return all(linea in resto for linea in _lineas_sql(plantilla))


def test_cada_plantilla_tiene_su_migracion_equivalente():
    plantillas = sorted((RAIZ / "platform" / "infra" / "rls-policy-templates").glob("*.sql"))
    assert plantillas
    for p in plantillas:
        m = RAIZ / "supabase" / "migrations" / p.name
        assert m.exists(), f"{p.name}: sin migración en supabase/migrations"
        assert _plantilla_contenida_en(p.read_text(encoding="utf-8"), m.read_text(encoding="utf-8")), (
            f"{p.name}: la migración no contiene el contenido de la plantilla"
        )


def test_plantilla_detecta_contenido_distinto():
    p = next((RAIZ / "platform" / "infra" / "rls-policy-templates").glob("*_platform_core.sql"))
    texto = p.read_text(encoding="utf-8")
    assert _plantilla_contenida_en(texto, texto)
    linea = next(x for x in texto.splitlines() if x.strip() and not x.strip().startswith("--"))
    assert not _plantilla_contenida_en(texto, texto.replace(linea, linea + " /* distinto */ x", 1))
    assert not _plantilla_contenida_en(texto, "")


# --- Campos de PII: filtro de Python vs beforeSend de cada frontend -------------------------------------------------


def _sentry_de_frontends() -> dict[str, Path]:
    """Cada `observabilidad/sentry.ts` bajo apps/* y platform/* (tolera cambios de nombre de carpeta)."""
    rutas = [
        *(RAIZ / "apps").glob("*/src/observabilidad/sentry.ts"),
        *(RAIZ / "platform").glob("*/src/observabilidad/sentry.ts"),
    ]
    return {r.relative_to(RAIZ).as_posix(): r for r in rutas}


# Según ARCHITECTURE.md §2.12 la web de reservas va a identificar con un código enviado por email y una sesión con
# token (todavía no existen): los saca de Sentry además de los de Python. En Python `codigo` es el código de error
# que llevan los logs, así que no puede ser PII allá.
CAMPOS_PII_SOLO_WEB = {"codigo", "token"}


def _campos_pii_ts(texto: str) -> set[str]:
    m = re.search(r"export const CAMPOS_PII[^=]*=\s*\[(.*?)\]", texto, re.S)
    assert m, "falta CAMPOS_PII"
    return set(re.findall(r'"([^"]+)"', m.group(1)))


def test_campos_pii_de_python_coinciden_con_el_before_send_de_cada_frontend():
    from turnos_observabilidad.pii import PII_CAMPOS

    frontends = _sentry_de_frontends()
    assert len(frontends) >= 4, f"se esperaban los sentry.ts de los 4 frontends: {sorted(frontends)}"
    for nombre, ruta in frontends.items():
        esperados = set(PII_CAMPOS) | (CAMPOS_PII_SOLO_WEB if nombre.startswith("apps/web-frontend/") else set())
        assert _campos_pii_ts(ruta.read_text(encoding="utf-8")) == esperados, nombre


def test_campos_pii_detecta_diferencia():
    from turnos_observabilidad.pii import PII_CAMPOS

    texto = next(iter(_sentry_de_frontends().values())).read_text(encoding="utf-8")
    roto = texto.replace('"phone",', "", 1)
    assert roto != texto
    assert _campos_pii_ts(roto) != set(PII_CAMPOS)


def test_campos_pii_de_la_web_suman_codigo_y_token_a_los_de_python():
    from turnos_observabilidad.pii import PII_CAMPOS

    ruta = RAIZ / "apps" / "web-frontend" / "src" / "observabilidad" / "sentry.ts"
    campos = _campos_pii_ts(ruta.read_text(encoding="utf-8"))
    assert {"email", "codigo", "token"} <= campos
    assert campos == set(PII_CAMPOS) | CAMPOS_PII_SOLO_WEB


# --- Slugs reservados: base / código Python / web-frontend ------------------------------------------------------------


def _reservados_web(texto: str) -> list[str]:
    m = re.search(r"export const SLUGS_RESERVADOS[^=]*=\s*\[(.*?)\]", texto, re.S)
    assert m, "falta SLUGS_RESERVADOS"
    return re.findall(r'"([^"]+)"', m.group(1))


def _reservados_migracion() -> set[str]:
    sql = re.sub(r"--[^\n]*", "", _leer("supabase/migrations/20260103003900_slugs_de_tenant.sql"))
    m = re.search(r"create function public\.slug_reservado.*?array\[(.*?)\]::text\[\]", sql, re.S)
    assert m, "no se encontró la lista en slug_reservado"
    return set(re.findall(r"'([^']+)'", m.group(1)))


def test_slugs_reservados_del_web_frontend_coinciden_con_la_base_y_con_python():
    from platform_admin.slugs_reservados import SLUGS_RESERVADOS

    web = _reservados_web(_leer("apps/web-frontend/src/slugs/reservados.ts"))
    assert len(web) == len(set(web)), "SLUGS_RESERVADOS del web-frontend tiene repetidos"
    assert set(web) == _reservados_migracion()
    assert set(web) == set(SLUGS_RESERVADOS)


def test_slugs_reservados_detecta_diferencia():
    texto = _leer("apps/web-frontend/src/slugs/reservados.ts")
    roto = re.sub(r'  "www",\r?\n', "", texto, count=1)
    assert roto != texto
    assert set(_reservados_web(roto)) != _reservados_migracion()


# --- Plantillas de turno del reclamo del job de vencimiento ---------------------------------------------------------


def _plantillas_de_turno_en_sql() -> tuple[list[frozenset[str]], frozenset[str]]:
    """(listas de plantillas de `reclamar_avisos_telegram`, las del cliente del CHECK sin las de cambio de horario)."""
    sql = re.sub(r"--[^\n]*", "", _leer("supabase/migrations/20260103004000_outbox_multicanal.sql"))
    funcion = sql[sql.index("create function public.reclamar_avisos_telegram") :]
    en_funcion = [
        frozenset(re.findall(r"'([a-z_]+)'", lista)) for lista in re.findall(r"template in \(([^)]*)\)", funcion)
    ]
    check = re.search(r"destino = 'cliente' and template in \(([^)]*)\)", sql)
    assert check, "falta el CHECK de plantillas del cliente"
    del_cliente = frozenset(re.findall(r"'([a-z_]+)'", check.group(1)))
    return en_funcion, frozenset(p for p in del_cliente if not p.startswith("cambio_horario_"))


def test_las_plantillas_de_turno_del_reclamo_coinciden_con_las_conocidas_y_con_el_check_de_la_tabla():
    from turnos_negocio import plantillas_conocidas

    en_funcion, del_check = _plantillas_de_turno_en_sql()
    assert len(en_funcion) == 3, "el reclamo tiene tres listas de plantillas (cierre sent, dead-letter y reclamo)"
    conocidas = plantillas_conocidas()
    assert del_check == conocidas, "plantillas del cliente del CHECK (sin cambio de horario) vs turnos_negocio"
    for lista in en_funcion:
        assert lista == conocidas, "cada lista de reclamar_avisos_telegram debe ser igual a plantillas_conocidas()"


def test_las_plantillas_y_canales_de_los_avisos_coinciden_con_el_check_de_la_tabla():
    """Las plantillas de `turnos_negocio` y los canales de `turnos_dominio` son los que admite el CHECK del outbox."""
    from turnos_dominio.avisos import CANALES_DE_AVISO
    from turnos_negocio import plantillas_cliente, plantillas_negocio

    sql = re.sub(r"--[^\n]*", "", _leer("supabase/migrations/20260103004000_outbox_multicanal.sql"))
    cliente = re.search(r"destino = 'cliente' and template in \(([^)]*)\)", sql)
    negocio = re.search(r"destino = 'negocio' and template in \(([^)]*)\)", sql)
    canales = re.search(r"channel is not null and channel in \(([^)]*)\)", sql)
    assert cliente and negocio and canales, "faltan los CHECK de plantillas o de canal del outbox"

    def _valores(texto: str) -> frozenset[str]:
        return frozenset(re.findall(r"'([a-z_]+)'", texto))

    assert _valores(cliente.group(1)) == plantillas_cliente()
    assert _valores(negocio.group(1)) - {"sincronizacion_calendar"} == plantillas_negocio()
    assert _valores(canales.group(1)) == CANALES_DE_AVISO
