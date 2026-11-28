"""/local/avisos contra Postgres real con el rol `dashboard_service`: default por rol, permisos, garantía de
destinatarios (también bajo escrituras simultáneas), auditoría con el actor y aislamiento entre tenants.

Datos y emails ficticios.
"""

import asyncio
import json
import uuid
from contextlib import asynccontextmanager

import pytest

pytestmark = pytest.mark.integracion

asyncpg = pytest.importorskip("asyncpg")

from dashboard_backend.tests.soporte import ORIGEN, http, token  # noqa: E402
from dashboard_backend.tests.soporte_staff import app_staff  # noqa: E402


class _PoolDeUnaConexion:
    def __init__(self, conn):
        self._conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self._conn


async def _borrar(conn, sql, *args):
    await conn.execute("set session_replication_role = replica")
    try:
        await conn.execute(sql, *args)
    finally:
        await conn.execute("set session_replication_role = origin")


@pytest.fixture
async def escenario(db_superuser):
    """Tenant A: owner, aprobador (approve_bookings), gestor (manage_staff), lectura (view_bookings). Tenant B: owner y
    un empleado."""
    sufijo = uuid.uuid4().hex[:8]
    ta, tb = uuid.uuid4(), uuid.uuid4()
    for t, n in ((ta, "a"), (tb, "b")):
        await db_superuser.execute(
            "insert into public.tenants (id, name, slug) values ($1, $2, $3)", t, n.upper(), f"it-avi-{n}-{sufijo}"
        )
    quienes = {
        "owner": (ta, None),
        "aprobador": (ta, ("approve_bookings", "view_bookings")),
        "gestor": (ta, ("manage_staff", "view_bookings")),
        "lectura": (ta, ("view_bookings",)),
        "b_owner": (tb, None),
        "b_staff": (tb, ("view_bookings",)),
    }
    auth, filas = {}, {}
    for nombre, (tenant, claves) in quienes.items():
        auth_id = uuid.uuid4()
        await db_superuser.execute(
            "insert into auth.users (id, email, raw_user_meta_data) values ($1, $2, $3::jsonb)",
            auth_id,
            f"{auth_id.hex[:12]}@prueba.invalid",
            json.dumps({"full_name": f"Persona {nombre}"}),
        )
        tu = await db_superuser.fetchval(
            "insert into public.tenant_users (tenant_id, auth_user_id, is_owner) values ($1, $2, $3) returning id",
            tenant,
            auth_id,
            claves is None,
        )
        for clave in claves or ():
            await db_superuser.execute(
                "insert into public.tenant_user_permissions (tenant_user_id, permission_key) values ($1, $2)", tu, clave
            )
        auth[nombre], filas[nombre] = auth_id, tu
    try:
        yield {"a": ta, "b": tb, "auth": auth, "fila": filas}
    finally:
        tenants = [ta, tb]
        async with db_superuser.transaction():
            await db_superuser.execute("alter table public.audit_log disable trigger audit_log_solo_agregar_row")
            await db_superuser.execute("delete from public.audit_log where tenant_id = any($1::uuid[])", tenants)
            await db_superuser.execute("alter table public.audit_log enable trigger audit_log_solo_agregar_row")
        # Sin triggers (réplica): borrar permisos o filas con los triggers de destinatarios activos fallaría según
        # quién haya quedado como único destinatario.
        await _borrar(
            db_superuser, "delete from public.avisos_negocio_config where tenant_id = any($1::uuid[])", tenants
        )
        await _borrar(
            db_superuser, "delete from public.tenant_user_permissions where tenant_id = any($1::uuid[])", tenants
        )
        await _borrar(db_superuser, "delete from public.tenant_users where tenant_id = any($1::uuid[])", tenants)
        await _borrar(db_superuser, "delete from public.tenants where id = any($1::uuid[])", tenants)
        await _borrar(db_superuser, "delete from auth.users where id = any($1::uuid[])", list(auth.values()))


@pytest.fixture
async def dashboard(conectar_como):
    return await conectar_como("dashboard_service")


def _h(escenario, quien, escribe=True):
    owner = quien in ("owner", "b_owner")
    tenant = escenario["b"] if quien.startswith("b_") else escenario["a"]
    h = {
        "Cookie": "dash_acceso="
        + token(
            user_id=escenario["auth"][quien],
            tenant_id=tenant,
            is_owner=owner,
            aal="aal2" if owner else "aal1",
        )
    }
    if escribe:
        h["Origin"] = ORIGEN
    return h


def _app(conn):
    app, _ = app_staff(pool=_PoolDeUnaConexion(conn))
    return app


def _cambio(fila, **kw):
    return {"staff_id": str(fila), **kw}


async def _put(app, escenario, quien, cambios):
    return await http(app).put("/local/avisos", json={"cambios": cambios}, headers=_h(escenario, quien))


async def _config(db, tenant):
    filas = await db.fetch(
        "select tenant_user_id, recibe_avisos, canal_email, canal_whatsapp from public.avisos_negocio_config "
        "where tenant_id = $1",
        tenant,
    )
    return {f["tenant_user_id"]: (f["recibe_avisos"], f["canal_email"], f["canal_whatsapp"]) for f in filas}


async def _auditoria(db, tenant):
    filas = await db.fetch(
        "select action, entity, actor_id, metadata::text as metadata from public.audit_log "
        "where tenant_id = $1 order by created_at, id",
        tenant,
    )
    return [(f["action"], f["entity"], f["actor_id"], json.loads(f["metadata"] or "{}")) for f in filas]


async def test_default_por_rol_y_permisos_de_lectura(dashboard, escenario):
    app = _app(dashboard)
    r = await http(app).get("/local/avisos", headers=_h(escenario, "owner", escribe=False))
    assert r.status_code == 200, r.text
    por_id = {f["id"]: f for f in r.json()["staff"]}
    assert set(por_id) == {str(escenario["fila"][n]) for n in ("owner", "aprobador", "gestor", "lectura")}
    recibe = {
        n: por_id[str(escenario["fila"][n])]["recibe_por_email"] for n in ("owner", "aprobador", "gestor", "lectura")
    }
    assert recibe == {"owner": True, "aprobador": True, "gestor": False, "lectura": False}
    assert all(f["es_default"] and f["canal_whatsapp"] is False for f in por_id.values())
    # `es_yo` marca solo a la persona de la sesión (la que llama), según su cuenta
    assert {n: por_id[str(escenario["fila"][n])]["es_yo"] for n in ("owner", "aprobador", "gestor", "lectura")} == {
        "owner": True,
        "aprobador": False,
        "gestor": False,
        "lectura": False,
    }
    r = await http(app).get("/local/avisos", headers=_h(escenario, "gestor", escribe=False))
    assert {f["id"] for f in r.json()["staff"] if f["es_yo"]} == {str(escenario["fila"]["gestor"])}
    # quien gestiona el personal lee; quien aprueba turnos o solo ve turnos, no
    assert (await http(app).get("/local/avisos", headers=_h(escenario, "gestor", escribe=False))).status_code == 200
    for quien in ("aprobador", "lectura"):
        assert (await http(app).get("/local/avisos", headers=_h(escenario, quien, escribe=False))).status_code == 403


async def test_el_gestor_cambia_y_vuelve_al_default_con_auditoria_del_actor(dashboard, escenario, db_superuser):
    app = _app(dashboard)
    lectura = escenario["fila"]["lectura"]
    r = await _put(app, escenario, "gestor", [_cambio(lectura, recibe_avisos=True, canal_email=True)])
    assert r.status_code == 200, r.text
    assert await _config(db_superuser, escenario["a"]) == {lectura: (True, True, False)}
    assert {f["id"]: f for f in r.json()["staff"]}[str(lectura)]["recibe_por_email"] is True
    # quien solo aprueba turnos no puede cambiar la configuración
    r = await _put(app, escenario, "aprobador", [_cambio(lectura, por_defecto=True)])
    assert r.status_code == 403
    assert await _config(db_superuser, escenario["a"]) == {lectura: (True, True, False)}
    r = await _put(app, escenario, "gestor", [_cambio(lectura, por_defecto=True)])
    assert r.status_code == 200, r.text
    assert await _config(db_superuser, escenario["a"]) == {}
    audit = await _auditoria(db_superuser, escenario["a"])
    assert [(a[0], a[1], a[2]) for a in audit] == [
        ("avisos_negocio_updated", "avisos_negocio_config", escenario["auth"]["gestor"])
    ] * 2
    assert audit[1][3]["cambios"][0]["por_defecto"] is True
    assert "@" not in json.dumps(audit, default=str)


@pytest.mark.parametrize("antes,despues", [((True, False), (False, True)), ((False, True), (True, False))])
async def test_insertar_y_actualizar_guardan_cada_columna_en_su_lugar(
    dashboard, escenario, db_superuser, antes, despues
):
    app = _app(dashboard)
    lectura = escenario["fila"]["lectura"]
    r = await _put(app, escenario, "owner", [_cambio(lectura, recibe_avisos=antes[0], canal_email=antes[1])])
    assert r.status_code == 200, r.text
    assert await _config(db_superuser, escenario["a"]) == {lectura: (antes[0], antes[1], False)}
    # la fila ya existe: ahora corre el UPDATE real, con las columnas invertidas
    r = await _put(app, escenario, "owner", [_cambio(lectura, recibe_avisos=despues[0], canal_email=despues[1])])
    assert r.status_code == 200, r.text
    assert await _config(db_superuser, escenario["a"]) == {lectura: (despues[0], despues[1], False)}
    f = {x["id"]: x for x in r.json()["staff"]}[str(lectura)]
    assert (f["recibe_avisos"], f["canal_email"], f["recibe_por_email"]) == (despues[0], despues[1], all(despues))
    audit = await _auditoria(db_superuser, escenario["a"])
    assert audit[-1][3]["cambios"] == [
        {"staff_id": str(lectura), "por_defecto": False, "recibe_avisos": despues[0], "canal_email": despues[1]}
    ]


async def test_no_se_deja_al_negocio_sin_destinatario_de_email(dashboard, escenario, db_superuser):
    app = _app(dashboard)
    owner, aprobador = escenario["fila"]["owner"], escenario["fila"]["aprobador"]
    r = await _put(app, escenario, "owner", [_cambio(aprobador, recibe_avisos=False, canal_email=True)])
    assert r.status_code == 200, r.text
    antes = await _config(db_superuser, escenario["a"])
    r = await _put(app, escenario, "owner", [_cambio(owner, recibe_avisos=True, canal_email=False)])
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "sin_destinatario_de_avisos"
    assert await _config(db_superuser, escenario["a"]) == antes
    assert len(await _auditoria(db_superuser, escenario["a"])) == 1  # solo el primer cambio
    # intercambiar al destinatario en un solo pedido es válido, en cualquier orden
    r = await _put(
        app,
        escenario,
        "owner",
        [
            _cambio(owner, recibe_avisos=False, canal_email=True),
            _cambio(aprobador, recibe_avisos=True, canal_email=True),
        ],
    )
    assert r.status_code == 200, r.text
    # valores asimétricos del owner: cada columna en su lugar tras el UPDATE real
    assert await _config(db_superuser, escenario["a"]) == {owner: (False, True, False), aprobador: (True, True, False)}


async def _apagar(app, escenario, quien):
    return await _put(app, escenario, "owner", [_cambio(quien, recibe_avisos=False, canal_email=True)])


@pytest.mark.parametrize("primero", ["owner", "aprobador"])
async def test_el_segundo_cambio_que_dejaria_en_cero_es_409_gane_quien_gane(
    dashboard, escenario, db_superuser, primero
):
    # los dos únicos destinatarios son owner y aprobador: apagar a ambos, en cualquiera de los dos órdenes
    app = _app(dashboard)
    fila = escenario["fila"]
    segundo = "aprobador" if primero == "owner" else "owner"
    assert (await _apagar(app, escenario, fila[primero])).status_code == 200
    r = await _apagar(app, escenario, fila[segundo])
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "sin_destinatario_de_avisos"
    assert await _config(db_superuser, escenario["a"]) == {fila[primero]: (False, True, False)}
    assert len(await _auditoria(db_superuser, escenario["a"])) == 1


async def test_dos_cambios_simultaneos_no_dejan_al_negocio_en_cero(conectar_como, escenario, db_superuser):
    # owner y aprobador son los dos únicos destinatarios: dos pedidos que apagan uno cada uno, a la vez. Quién gana
    # no está fijado (lo decide el orden en que toman el lock del negocio): el resultado vale para cualquiera.
    a, b = await conectar_como("dashboard_service"), await conectar_como("dashboard_service")
    owner, aprobador = escenario["fila"]["owner"], escenario["fila"]["aprobador"]
    r1, r2 = await asyncio.gather(_apagar(_app(a), escenario, owner), _apagar(_app(b), escenario, aprobador))
    assert sorted([r1.status_code, r2.status_code]) == [200, 409], (r1.text, r2.text)
    ganador, apagado = (owner, r1) if r1.status_code == 200 else (aprobador, r2)
    perdedor = r2 if apagado is r1 else r1
    assert perdedor.json()["detail"]["codigo"] == "sin_destinatario_de_avisos"
    assert await _config(db_superuser, escenario["a"]) == {ganador: (False, True, False)}
    assert len(await _auditoria(db_superuser, escenario["a"])) == 1
    recibe = await db_superuser.fetchval("select public.avisos_negocio_destinatarios_email($1)", escenario["a"])
    assert recibe >= 1


async def test_whatsapp_no_se_activa_y_una_persona_desactivada_no_se_configura(dashboard, escenario, db_superuser):
    app = _app(dashboard)
    lectura = escenario["fila"]["lectura"]
    r = await _put(
        app, escenario, "owner", [_cambio(lectura, recibe_avisos=True, canal_email=True, canal_whatsapp=True)]
    )
    assert r.status_code == 422 and r.json()["detail"]["codigo"] == "canal_no_disponible"
    await db_superuser.execute("update public.tenant_users set is_active = false where id = $1", lectura)
    r = await _put(app, escenario, "owner", [_cambio(lectura, recibe_avisos=True, canal_email=True)])
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "staff_inactivo"
    assert await _config(db_superuser, escenario["a"]) == {}
    assert await _auditoria(db_superuser, escenario["a"]) == []


async def test_un_negocio_no_ve_ni_modifica_la_configuracion_de_otro(dashboard, escenario, db_superuser):
    app = _app(dashboard)
    ajeno, propio = escenario["fila"]["b_staff"], escenario["fila"]["lectura"]
    r = await _put(app, escenario, "owner", [_cambio(ajeno, recibe_avisos=True, canal_email=True)])
    assert r.status_code == 422 and r.json()["detail"]["codigo"] == "staff_inexistente"
    r = await _put(
        app,
        escenario,
        "owner",
        [_cambio(propio, recibe_avisos=True, canal_email=True), _cambio(ajeno, por_defecto=True)],
    )
    assert r.status_code == 422
    assert await _config(db_superuser, escenario["a"]) == {} and await _config(db_superuser, escenario["b"]) == {}
    # el otro negocio ve solo a los suyos y su configuración no aparece en la del primero
    r = await _put(app, escenario, "b_owner", [_cambio(ajeno, recibe_avisos=True, canal_email=True)])
    assert r.status_code == 200, r.text
    assert {f["id"] for f in r.json()["staff"]} == {str(escenario["fila"]["b_owner"]), str(ajeno)}
    assert await _config(db_superuser, escenario["a"]) == {}
    assert await _auditoria(db_superuser, escenario["a"]) == []
    assert len(await _auditoria(db_superuser, escenario["b"])) == 1
    vista_a = await http(app).get("/local/avisos", headers=_h(escenario, "owner", escribe=False))
    assert str(ajeno) not in vista_a.text
