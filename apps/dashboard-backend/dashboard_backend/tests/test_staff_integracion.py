"""/staff contra Postgres real con el rol `dashboard_service`: escalación de privilegios, último owner, auditoría
y aislamiento entre tenants. La API de Auth Admin va con el HTTP mockeado; datos y emails ficticios.
"""

import json
import uuid
from contextlib import asynccontextmanager

import pytest

pytestmark = pytest.mark.integracion

asyncpg = pytest.importorskip("asyncpg")

from dashboard_backend.tests.soporte import ORIGEN, http, token  # noqa: E402
from dashboard_backend.tests.soporte_staff import app_staff, auth_admin_http  # noqa: E402


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
    """Tenant A: owner, gestor (manage_staff), lectura (view_bookings). Tenant B: owner y un empleado."""
    sufijo = uuid.uuid4().hex[:8]
    ta, tb = uuid.uuid4(), uuid.uuid4()
    for t, n in ((ta, "a"), (tb, "b")):
        await db_superuser.execute(
            "insert into public.tenants (id, name, slug) values ($1, $2, $3)", t, n.upper(), f"it-stf-{n}-{sufijo}"
        )
    quienes = {
        "owner": (ta, None),
        "gestor": (ta, ("manage_staff", "view_bookings")),
        "lectura": (ta, ("view_bookings",)),
        "b_owner": (tb, None),
        "b_staff": (tb, ("view_bookings",)),
    }
    auth, filas, correos = {}, {}, {}
    for nombre, (tenant, claves) in quienes.items():
        auth_id = uuid.uuid4()
        correo = f"{auth_id.hex[:12]}@prueba.invalid"
        await db_superuser.execute(
            "insert into auth.users (id, email, raw_user_meta_data) values ($1, $2, $3::jsonb)",
            auth_id,
            correo,
            json.dumps({"full_name": f"Persona {nombre}"}),
        )
        correos[nombre] = correo
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
    # Cuenta de Auth que "creará" la API de invitaciones (la fila de tenant_users la crea el endpoint).
    invitado = uuid.uuid4()
    await db_superuser.execute(
        "insert into auth.users (id, email) values ($1, $2)", invitado, f"{invitado.hex[:12]}@prueba.invalid"
    )
    auth["invitado"] = invitado
    try:
        yield {"a": ta, "b": tb, "auth": auth, "fila": filas, "correo": correos}
    finally:
        tenants = [ta, tb]
        async with db_superuser.transaction():
            await db_superuser.execute("alter table public.audit_log disable trigger audit_log_solo_agregar_row")
            await db_superuser.execute("delete from public.audit_log where tenant_id = any($1::uuid[])", tenants)
            await db_superuser.execute("alter table public.audit_log enable trigger audit_log_solo_agregar_row")
            await db_superuser.execute(
                "delete from public.tenant_user_permissions where tenant_id = any($1::uuid[])", tenants
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


def _app(dashboard, **kw):
    app, _ = app_staff(pool=_PoolDeUnaConexion(dashboard), **kw)
    return app


async def _estado(db, fila):
    r = await db.fetchrow("select is_owner, is_active from public.tenant_users where id = $1", fila)
    return r["is_owner"], r["is_active"]


async def _permisos(db, fila):
    filas = await db.fetch(
        "select permission_key from public.tenant_user_permissions where tenant_user_id = $1 order by 1", fila
    )
    return [f["permission_key"] for f in filas]


async def _auditoria(db, tenant):
    filas = await db.fetch(
        "select action, entity, entity_id, metadata::text as metadata from public.audit_log "
        "where tenant_id = $1 order by created_at, id",
        tenant,
    )
    return [(f["action"], f["entity"], f["entity_id"], json.loads(f["metadata"] or "{}")) for f in filas]


# --- listar y aislamiento -------------------------------------------------------------------------------------


async def test_listar_devuelve_solo_el_staff_del_tenant_con_sus_permisos(dashboard, escenario):
    app = _app(dashboard)
    r = await http(app).get("/staff", headers=_h(escenario, "owner", escribe=False))
    assert r.status_code == 200, r.text
    por_id = {f["id"]: f for f in r.json()["staff"]}
    assert set(por_id) == {str(escenario["fila"][n]) for n in ("owner", "gestor", "lectura")}
    assert por_id[str(escenario["fila"]["gestor"])]["permission_keys"] == ["manage_staff", "view_bookings"]
    assert por_id[str(escenario["fila"]["owner"])]["is_owner"] is True
    assert por_id[str(escenario["fila"]["owner"])]["es_yo"] is True
    assert all("auth_user_id" not in f and "pending_invite_email" not in f for f in por_id.values())
    # el gestor lista; quien solo ve turnos, no
    assert (await http(app).get("/staff", headers=_h(escenario, "gestor", escribe=False))).status_code == 200
    assert (await http(app).get("/staff", headers=_h(escenario, "lectura", escribe=False))).status_code == 403


async def test_owner_de_un_tenant_no_ve_ni_modifica_staff_de_otro(dashboard, escenario, db_superuser):
    app = _app(dashboard)
    ajeno = escenario["fila"]["b_staff"]
    antes = (await _estado(db_superuser, ajeno), await _permisos(db_superuser, ajeno))
    h = _h(escenario, "owner")
    assert (
        await http(app).put(f"/staff/{ajeno}/permisos", json={"permission_keys": ["manage_staff"]}, headers=h)
    ).status_code == 404
    assert (await http(app).put(f"/staff/{ajeno}/owner", json={"is_owner": True}, headers=h)).status_code == 404
    assert (await http(app).post(f"/staff/{ajeno}/desactivar", headers=h)).status_code == 404
    assert (await http(app).post(f"/staff/{escenario['fila']['b_owner']}/desactivar", headers=h)).status_code == 404
    assert (await http(app).post(f"/staff/{ajeno}/reactivar", headers=h)).status_code == 404
    assert antes == (await _estado(db_superuser, ajeno), await _permisos(db_superuser, ajeno))
    assert await _estado(db_superuser, escenario["fila"]["b_owner"]) == (True, True)
    assert await _auditoria(db_superuser, escenario["b"]) == []


# --- escalación de privilegios --------------------------------------------------------------------------------


async def test_gestor_con_manage_staff_no_puede_autopromoverse_ni_dar_permisos(dashboard, escenario, db_superuser):
    app = _app(dashboard, cliente_admin=auth_admin_http(escenario["auth"]["invitado"]))
    h = _h(escenario, "gestor")
    yo = escenario["fila"]["gestor"]
    r = await http(app).put(f"/staff/{yo}/owner", json={"is_owner": True}, headers=h)
    assert r.status_code == 403 and r.json()["detail"]["codigo"] == "no_es_owner"
    r = await http(app).put(
        f"/staff/{yo}/permisos", json={"permission_keys": ["manage_staff", "edit_prices"]}, headers=h
    )
    assert r.status_code == 403
    r = await http(app).put(
        f"/staff/{escenario['fila']['lectura']}/permisos", json={"permission_keys": ["edit_prices"]}, headers=h
    )
    assert r.status_code == 403
    r = await http(app).post(
        "/staff/invitaciones",
        json={"email": "nuevo@ejemplo.invalid", "nombre": "Ana Pérez", "permission_keys": []},
        headers=h,
    )
    assert r.status_code == 403
    assert await _estado(db_superuser, yo) == (False, True)
    assert await _permisos(db_superuser, yo) == ["manage_staff", "view_bookings"]
    assert await _permisos(db_superuser, escenario["fila"]["lectura"]) == ["view_bookings"]
    assert await _auditoria(db_superuser, escenario["a"]) == []


async def test_gestor_no_desactiva_a_un_owner_pero_si_a_un_empleado(dashboard, escenario, db_superuser):
    app = _app(dashboard)
    h = _h(escenario, "gestor")
    r = await http(app).post(f"/staff/{escenario['fila']['owner']}/desactivar", headers=h)
    assert r.status_code == 403 and r.json()["detail"]["codigo"] == "no_es_owner"
    assert await _estado(db_superuser, escenario["fila"]["owner"]) == (True, True)
    r = await http(app).post(f"/staff/{escenario['fila']['lectura']}/desactivar", headers=h)
    assert r.status_code == 200 and r.json()["is_active"] is False


# --- último owner ---------------------------------------------------------------------------------------------


async def test_el_ultimo_owner_no_se_desactiva_ni_pierde_is_owner_409(dashboard, escenario, db_superuser):
    app = _app(dashboard)
    h = _h(escenario, "owner")
    yo = escenario["fila"]["owner"]
    r = await http(app).post(f"/staff/{yo}/desactivar", headers=h)
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "ultimo_owner"
    assert "owner" in r.json()["detail"]["mensaje"]
    r = await http(app).put(f"/staff/{yo}/owner", json={"is_owner": False}, headers=h)
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "ultimo_owner"
    assert await _estado(db_superuser, yo) == (True, True)
    assert await _auditoria(db_superuser, escenario["a"]) == []  # lo rechazado hizo ROLLBACK, sin audit


async def test_con_dos_owners_uno_puede_dejar_de_serlo_y_el_otro_queda_protegido(dashboard, escenario, db_superuser):
    app = _app(dashboard)
    gestor = escenario["fila"]["gestor"]
    r = await http(app).put(f"/staff/{gestor}/owner", json={"is_owner": True}, headers=_h(escenario, "owner"))
    assert r.status_code == 200 and r.json()["is_owner"] is True
    # el primer owner se baja a sí mismo (ahora hay otro)
    yo = escenario["fila"]["owner"]
    r = await http(app).put(f"/staff/{yo}/owner", json={"is_owner": False}, headers=_h(escenario, "owner"))
    assert r.status_code == 200 and r.json()["is_owner"] is False
    # el que queda es el último: no se puede tocar
    nuevo_owner = {
        "Cookie": "dash_acceso="
        + token(user_id=escenario["auth"]["gestor"], tenant_id=escenario["a"], is_owner=True, aal="aal2"),
        "Origin": ORIGEN,
    }
    r = await http(app).post(f"/staff/{gestor}/desactivar", headers=nuevo_owner)
    assert r.status_code == 409 and r.json()["detail"]["codigo"] == "ultimo_owner"
    assert [a[0] for a in await _auditoria(db_superuser, escenario["a"])] == [
        "staff_owner_granted",
        "staff_owner_revoked",
    ]


# --- permisos, baja y auditoría -------------------------------------------------------------------------------


async def test_owner_cambia_permisos_y_queda_en_audit_log_sin_pii(dashboard, escenario, db_superuser):
    app = _app(dashboard)
    destino = escenario["fila"]["lectura"]
    r = await http(app).put(
        f"/staff/{destino}/permisos",
        json={"permission_keys": ["view_bookings", "edit_prices"]},
        headers=_h(escenario, "owner"),
    )
    assert r.status_code == 200, r.text
    assert await _permisos(db_superuser, destino) == ["edit_prices", "view_bookings"]
    r = await http(app).put(
        f"/staff/{destino}/permisos", json={"permission_keys": ["clave_inventada"]}, headers=_h(escenario, "owner")
    )
    assert r.status_code == 422 and r.json()["detail"]["codigo"] == "permiso_desconocido"
    assert await _permisos(db_superuser, destino) == ["edit_prices", "view_bookings"]
    audit = await _auditoria(db_superuser, escenario["a"])
    assert len(audit) == 1
    accion, entidad, entidad_id, metadata = audit[0]
    assert (accion, entidad, entidad_id) == ("staff_permissions_changed", "tenant_user", destino)
    assert metadata == {"agregados": ["edit_prices"], "quitados": []}


async def test_baja_y_reactivacion_conservan_la_fila_y_cortan_el_acceso(dashboard, escenario, db_superuser):
    app = _app(dashboard)
    destino = escenario["fila"]["lectura"]
    filas_antes = await db_superuser.fetchval(
        "select count(*) from public.tenant_users where tenant_id = $1", escenario["a"]
    )
    r = await http(app).post(f"/staff/{destino}/desactivar", headers=_h(escenario, "owner"))
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert (
        await db_superuser.fetchval("select count(*) from public.tenant_users where tenant_id = $1", escenario["a"])
        == filas_antes
    )
    assert (await _estado(db_superuser, destino))[1] is False
    # el desactivado ya no entra (estado leído en vivo)
    assert (await http(app).get("/staff", headers=_h(escenario, "lectura", escribe=False))).status_code == 403
    r = await http(app).post(f"/staff/{destino}/reactivar", headers=_h(escenario, "owner"))
    assert r.status_code == 200 and r.json()["is_active"] is True
    assert await _permisos(db_superuser, destino) == ["view_bookings"]
    assert [a[0] for a in await _auditoria(db_superuser, escenario["a"])] == ["staff_deactivated", "staff_reactivated"]


async def test_owner_invita_y_el_nuevo_staff_queda_con_sus_permisos(dashboard, escenario, db_superuser):
    app = _app(dashboard, cliente_admin=auth_admin_http(escenario["auth"]["invitado"]))
    r = await http(app).post(
        "/staff/invitaciones",
        json={
            "email": "nuevo@ejemplo.invalid",
            "nombre": "Ana Pérez",
            "permission_keys": ["view_bookings", "view_reports"],
        },
        headers=_h(escenario, "owner"),
    )
    assert r.status_code == 201, r.text
    nuevo = uuid.UUID(r.json()["tenant_user_id"])
    assert "ejemplo.invalid" not in r.text
    assert await _estado(db_superuser, nuevo) == (False, True)
    assert await _permisos(db_superuser, nuevo) == ["view_bookings", "view_reports"]
    audit = await _auditoria(db_superuser, escenario["a"])
    assert [a[0] for a in audit] == ["staff_invited"]
    assert "ejemplo.invalid" not in json.dumps(audit, default=str)
    # reintentar la misma invitación no duplica ni cambia permisos
    r = await http(app).post(
        "/staff/invitaciones",
        json={"email": "nuevo@ejemplo.invalid", "nombre": "Ana Pérez", "permission_keys": []},
        headers=_h(escenario, "owner"),
    )
    assert r.status_code == 200 and r.json()["creado"] is False
    assert await _permisos(db_superuser, nuevo) == ["view_bookings", "view_reports"]


async def test_invitar_con_la_api_de_auth_caida_no_deja_filas(dashboard, escenario, db_superuser):
    app = _app(dashboard, cliente_admin=auth_admin_http(escenario["auth"]["invitado"], estado=500))
    antes = await db_superuser.fetchval("select count(*) from public.tenant_users where tenant_id = $1", escenario["a"])
    r = await http(app).post(
        "/staff/invitaciones",
        json={"email": "nuevo@ejemplo.invalid", "nombre": "Ana Pérez", "permission_keys": []},
        headers=_h(escenario, "owner"),
    )
    assert r.status_code == 503 and r.json()["detail"]["reintentable"] is True
    assert (
        await db_superuser.fetchval("select count(*) from public.tenant_users where tenant_id = $1", escenario["a"])
        == antes
    )


# --- identidad (nombre y email) -------------------------------------------------------------------------------


async def test_listar_trae_nombre_y_email_de_cada_persona_y_nunca_los_de_otro_tenant(dashboard, escenario):
    app = _app(dashboard)
    for quien in ("owner", "gestor"):  # owner y quien tiene manage_staff
        r = await http(app).get("/staff", headers=_h(escenario, quien, escribe=False))
        assert r.status_code == 200, r.text
        por_id = {f["id"]: f for f in r.json()["staff"]}
        for n in ("owner", "gestor", "lectura"):
            f = por_id[str(escenario["fila"][n])]
            assert (f["nombre"], f["email"]) == (f"Persona {n}", escenario["correo"][n])
        assert "Persona b_" not in r.text and escenario["correo"]["b_owner"] not in r.text
    assert (await http(app).get("/staff", headers=_h(escenario, "lectura", escribe=False))).status_code == 403


async def test_staff_con_invitacion_pendiente_sale_con_email_y_nombre_null(dashboard, escenario, db_superuser):
    pendiente = await db_superuser.fetchval(
        "insert into public.tenant_users (tenant_id, pending_invite_email) values ($1, $2) returning id",
        escenario["a"],
        "pendiente@ejemplo.invalid",
    )
    r = await http(_app(dashboard)).get("/staff", headers=_h(escenario, "owner", escribe=False))
    fila = {f["id"]: f for f in r.json()["staff"]}[str(pendiente)]
    assert (fila["nombre"], fila["email"]) == (None, "pendiente@ejemplo.invalid")


async def test_la_funcion_de_identidad_rechaza_pedir_otro_tenant_desde_el_dashboard(dashboard, escenario):
    tx = dashboard.transaction()
    await tx.start()
    try:
        await dashboard.execute("select set_config('app.current_tenant_id', $1, true)", str(escenario["a"]))
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await dashboard.fetch("select * from public.get_tenant_staff_identity($1)", escenario["b"])
    finally:
        await tx.rollback()


async def test_invitar_envia_el_nombre_a_la_api_y_exige_nombre_sin_dejar_filas(dashboard, escenario, db_superuser):
    peticiones: list = []
    app = _app(dashboard, cliente_admin=auth_admin_http(escenario["auth"]["invitado"], peticiones=peticiones))
    antes = await db_superuser.fetchval("select count(*) from public.tenant_users where tenant_id = $1", escenario["a"])
    r = await http(app).post(
        "/staff/invitaciones",
        json={"email": "nuevo@ejemplo.invalid", "permission_keys": []},
        headers=_h(escenario, "owner"),
    )
    assert r.status_code == 422 and r.json()["detail"]["codigo"] == "nombre_invalido"
    assert peticiones == []
    assert (
        await db_superuser.fetchval("select count(*) from public.tenant_users where tenant_id = $1", escenario["a"])
        == antes
    )
    r = await http(app).post(
        "/staff/invitaciones",
        json={"email": "nuevo@ejemplo.invalid", "nombre": "  Nuevo   Nombre ", "permission_keys": []},
        headers=_h(escenario, "owner"),
    )
    assert r.status_code == 201, r.text
    assert peticiones == [{"email": "nuevo@ejemplo.invalid", "data": {"full_name": "Nuevo Nombre"}}]
    assert "Nuevo" not in json.dumps(await _auditoria(db_superuser, escenario["a"]), default=str)
