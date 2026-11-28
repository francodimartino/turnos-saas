"""Chequeos estáticos (sin base de datos) de las migraciones de los roles web_reservas y enviador.

- Los roles nacen como el resto de los roles de login: LOGIN, sin superuser, sin BYPASSRLS, sin herencia y sin
  contraseña versionada.
- Los privilegios que otorgan las migraciones son EXACTAMENTE los esperados (tabla, columnas y función): un grant de
  más o de menos hace fallar el test, y nada se otorga a otro rol ni con GRANT OPTION.
- El enviador no tiene ningún privilegio de tabla.
- La web nunca escribe `created_at` de codigos_web ni de sesiones_web ni lo que pone la base al insertar.
- Cada migración solo otorga sobre objetos que ya existen cuando ella corre.
La comparación contra la base real (con los roles reales) está en `supabase/tests/turnos_78_*` y `turnos_79_*`.
"""

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
MIGRACIONES = RAIZ / "supabase" / "migrations"
ROLES_Y_GRANTS = MIGRACIONES / "20260103004100_roles_web_y_enviador.sql"
GRANTS_TABLAS_WEB = MIGRACIONES / "20260103004470_grants_web_sesiones_codigos_y_politica.sql"
ENTREGAS_ENVIADOR = MIGRACIONES / "20260103004490_entregas_outbox_enviador.sql"

WEB = "web_reservas"
ENVIADOR = "enviador"
CATALOGO = ("services", "professionals", "professional_services", "availability_rules", "availability_blocks")

# (privilegio, columnas ordenadas o None si es de tabla/función, objeto, rol)
Fila = tuple[str, tuple[str, ...] | None, str, str]


def _sql(ruta: Path) -> str:
    lineas = [x for x in ruta.read_text(encoding="utf-8").splitlines() if not x.lstrip().startswith("--")]
    return re.sub(r"\s+", " ", " ".join(lineas)).strip().lower()


def _grants(sql: str) -> set[Fila]:
    """Una fila por privilegio, objeto y rol de cada `grant` del texto."""
    filas: set[Fila] = set()
    for m in re.finditer(r"grant ([a-z, ]+?)(?: \(([^)]*)\))? on (function )?([^;]+?) to ([^;]+);", sql):
        privilegios, columnas, es_funcion, objetos, roles = m.groups()
        cols = tuple(sorted(c.strip() for c in columnas.split(","))) if columnas else None
        lista = [objetos] if es_funcion else [o.strip() for o in objetos.split(",")]
        for privilegio in (p.strip() for p in privilegios.split(",")):
            for objeto in lista:
                for rol in (r.strip() for r in roles.split(",")):
                    prefijo = "function " if es_funcion else ""
                    filas.add((privilegio, cols, prefijo + objeto.removeprefix("public."), rol))
    return filas


def _cols(*nombres: str) -> tuple[str, ...]:
    return tuple(sorted(nombres))


def _funciones(rol: str, *firmas: str) -> set[Fila]:
    return {("execute", None, f"function {firma}", rol) for firma in firmas}


def _esperado_web_roles() -> set[Fila]:
    filas: set[Fila] = {("select", None, tabla, WEB) for tabla in CATALOGO}
    filas |= {(privilegio, None, "bookings", WEB) for privilegio in ("select", "insert", "update")}
    filas |= {
        ("select", _cols("id", "name", "settings"), "tenants", WEB),
        (
            "select",
            _cols("id", "tenant_id", "name", "phone", "email", "created_at", "deleted_at"),
            "end_customers",
            WEB,
        ),
        ("insert", _cols("tenant_id", "name", "phone", "email"), "end_customers", WEB),
        ("update", _cols("name", "phone", "email"), "end_customers", WEB),
        ("select", None, "canales_cliente", WEB),
        (
            "insert",
            _cols("tenant_id", "end_customer_id", "canal", "identificador", "verificado_at"),
            "canales_cliente",
            WEB,
        ),
        ("update", _cols("verificado_at"), "canales_cliente", WEB),
        ("select", _cols("id", "tenant_id", "used_sessions"), "booking_packs", WEB),
        ("update", _cols("used_sessions"), "booking_packs", WEB),
    }
    filas |= _funciones(
        WEB,
        "resolver_slug_tenant(text)",
        "anonymize_end_customer(uuid)",
        "enqueue_notification(uuid, text, text, jsonb, integer)",
        "encolar_aviso_negocio(text, jsonb, integer)",
    )
    return filas


def _esperado_enviador() -> set[Fila]:
    return _funciones(
        ENVIADOR,
        "claim_notification_outbox(integer, integer, integer)",
        "complete_notification_outbox(uuid, text, text, integer)",
        "resolve_outbox_delivery(uuid)",
        "registrar_entrega_outbox(uuid, text, text, text)",
        "find_integration(text, uuid)",
        "get_integration_secret(uuid)",
        "record_notification_attempt(uuid, text, text, text, integer, text, uuid)",
    )


def _esperado_web_tablas_de_la_web() -> set[Fila]:
    politica = _cols("politica_version", "politica_aceptada_at")
    return {
        ("select", None, "sesiones_web", WEB),
        ("insert", _cols("tenant_id", "end_customer_id", "token_hash", "expira_at"), "sesiones_web", WEB),
        ("update", _cols("revocada_at"), "sesiones_web", WEB),
        ("select", None, "codigos_web", WEB),
        ("insert", _cols("tenant_id", "email_hash", "codigo_hash", "expira_at", "ip_hash"), "codigos_web", WEB),
        ("update", _cols("intentos", "usado_at"), "codigos_web", WEB),
        ("select", politica, "end_customers", WEB),
        ("insert", politica, "end_customers", WEB),
        ("update", politica, "end_customers", WEB),
    }


def _comparar(real: set[Fila], esperado: set[Fila]) -> None:
    assert real - esperado == set(), f"grants de más: {sorted(real - esperado, key=str)}"
    assert esperado - real == set(), f"grants que faltan: {sorted(esperado - real, key=str)}"


def test_los_roles_nacen_de_minimo_privilegio_y_sin_contrasena():
    sql = _sql(ROLES_Y_GRANTS)
    assert "foreach v_rol in array array['web_reservas', 'enviador']" in sql
    assert "'create role %i login nosuperuser nocreatedb nocreaterole noinherit noreplication nobypassrls'" in sql
    assert "password" not in sql
    assert "superuser" not in sql.replace("nosuperuser", "")
    assert "bypassrls" not in sql.replace("nobypassrls", "")


def test_los_grants_de_la_migracion_de_roles_son_exactos():
    _comparar(_grants(_sql(ROLES_Y_GRANTS)), _esperado_web_roles() | _esperado_enviador())


def test_la_migracion_de_las_entregas_solo_agrega_un_execute_para_el_enviador():
    sql = _sql(ENTREGAS_ENVIADOR)
    _comparar(_grants(sql), _funciones(ENVIADOR, "consultar_entregas_outbox(uuid)"))
    assert (
        "revoke all on function public.consultar_entregas_outbox(uuid) from public, anon, authenticated, service_role"
        in sql
    )
    assert "security definer" in sql and "set search_path = ''" in sql
    # `create or replace` conserva los grants del reclamo: la migración no los toca
    assert (
        "claim_notification_outbox" in sql and "grant execute on function public.claim_notification_outbox" not in sql
    )


def test_los_grants_de_las_tablas_de_la_web_son_exactos():
    _comparar(_grants(_sql(GRANTS_TABLAS_WEB)), _esperado_web_tablas_de_la_web())


def test_solo_se_otorga_a_los_dos_roles_y_sin_grant_option():
    for ruta in (ROLES_Y_GRANTS, GRANTS_TABLAS_WEB):
        sql = _sql(ruta)
        assert "with grant option" not in sql and "with admin option" not in sql, ruta.name
        assert "revoke" not in sql, ruta.name
        assert {fila[3] for fila in _grants(sql)} <= {WEB, ENVIADOR}, ruta.name


def test_el_enviador_solo_tiene_execute_sobre_funciones():
    for ruta in (ROLES_Y_GRANTS, GRANTS_TABLAS_WEB):
        for privilegio, _columnas, objeto, rol in _grants(_sql(ruta)):
            if rol == ENVIADOR:
                assert privilegio == "execute" and objeto.startswith("function "), (ruta.name, privilegio, objeto)


def test_nadie_borra_ni_trunca():
    for ruta in (ROLES_Y_GRANTS, GRANTS_TABLAS_WEB):
        for privilegio, _columnas, objeto, _rol in _grants(_sql(ruta)):
            assert privilegio in ("select", "insert", "update", "execute"), (ruta.name, privilegio, objeto)


def test_la_web_no_escribe_lo_que_pone_la_base_al_insertar():
    escrituras = {
        (objeto, privilegio): cols
        for privilegio, cols, objeto, rol in _grants(_sql(GRANTS_TABLAS_WEB)) | _grants(_sql(ROLES_Y_GRANTS))
        if rol == WEB and privilegio in ("insert", "update") and cols
    }
    for (objeto, privilegio), cols in escrituras.items():
        assert "created_at" not in cols, (objeto, privilegio)
        assert "id" not in cols, (objeto, privilegio)
    assert not {"intentos", "usado_at"} & set(escrituras[("codigos_web", "insert")])
    assert escrituras[("codigos_web", "update")] == _cols("intentos", "usado_at")


def _creadoras(nombre_tabla: str) -> list[str]:
    patron = f"create table public.{nombre_tabla} "
    return [m.name.split("_")[0] for m in MIGRACIONES.glob("*.sql") if patron in _sql(m)]


def _funcion_creada_en(firma: str) -> list[str]:
    nombre = firma.split("(")[0]
    return [m.name.split("_")[0] for m in MIGRACIONES.glob("*.sql") if f"create function public.{nombre}(" in _sql(m)]


def test_cada_migracion_otorga_solo_sobre_objetos_que_ya_existen():
    for ruta in (ROLES_Y_GRANTS, GRANTS_TABLAS_WEB):
        numero = ruta.name.split("_")[0]
        for _privilegio, _columnas, objeto, _rol in _grants(_sql(ruta)):
            if objeto.startswith("function "):
                creadoras = _funcion_creada_en(objeto.removeprefix("function "))
            else:
                creadoras = _creadoras(objeto)
            assert creadoras and all(n < numero for n in creadoras), (ruta.name, objeto, creadoras)


def test_las_columnas_de_la_politica_se_otorgan_despues_de_crearse():
    creadoras = [
        m.name.split("_")[0]
        for m in MIGRACIONES.glob("*.sql")
        if "add column if not exists politica_version" in _sql(m)
    ]
    assert creadoras
    assert all(n < GRANTS_TABLAS_WEB.name.split("_")[0] for n in creadoras)
    assert all(n > ROLES_Y_GRANTS.name.split("_")[0] for n in creadoras)


def test_el_detector_de_grants_ve_un_grant_de_mas():
    sql = "grant select on public.audit_log to web_reservas; grant select (id) on public.bookings to enviador;"
    filas = _grants(sql)
    assert ("select", None, "audit_log", WEB) in filas
    assert ("select", ("id",), "bookings", ENVIADOR) in filas
    try:
        _comparar(filas, set())
    except AssertionError as error:
        assert "grants de más" in str(error)
    else:
        raise AssertionError("el comparador no detectó un grant de más")
