"""Visores, integraciones e impersonación contra Postgres real, con dos tenants.

El admin entra por `conectar_como("platform_admin")` (visores, audit) y la impersonación lee por
`conectar_como("platform_admin_readonly")`. `dashboard_service` se usa para comprobar que el tenant no ve el audit
de la impersonación.
"""

import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
import turnos_queries as tq
from platform_tenancy import readonly_tx, tenant_tx

pytestmark = pytest.mark.integracion

asyncpg = pytest.importorskip("asyncpg")

from admin_backend.tests.soporte import app_de_prueba, bearer, http  # noqa: E402

DESDE = datetime(2030, 1, 1, tzinfo=timezone.utc)  # noqa: UP017
HASTA = DESDE + timedelta(days=7)
F_DESDE = DESDE.isoformat().replace("+00:00", "Z")
F_HASTA = HASTA.isoformat().replace("+00:00", "Z")


class _PoolDeUnaConexion:
    def __init__(self, conn):
        self._conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self._conn


@pytest.fixture
async def escenario(db_superuser):
    """Un platform admin real y dos tenants (A y B) con servicio, profesional, cliente, pack y un booking cada uno."""
    sufijo = uuid.uuid4().hex[:8]
    admin, staff_a = uuid.uuid4(), uuid.uuid4()
    for u in (admin, staff_a):
        await db_superuser.execute(
            "insert into auth.users (id, email) values ($1, $2)", u, f"{u.hex[:12]}@prueba.invalid"
        )
    await db_superuser.execute("insert into public.platform_admins (auth_user_id) values ($1)", admin)
    datos = {"admin": admin, "staff_a": staff_a}
    for k in ("a", "b"):
        d = {n: uuid.uuid4() for n in ("tenant", "prof", "servicio", "cliente", "pack", "booking")}
        datos[k] = d
        await db_superuser.execute(
            "insert into public.tenants (id, name, slug) values ($1, $2, $3)",
            d["tenant"],
            f"Tenant {k.upper()}",
            f"it-imp-{k}-{sufijo}",
        )
        await db_superuser.execute(
            "insert into public.professionals (id, tenant_id, name) values ($1, $2, $3)",
            d["prof"],
            d["tenant"],
            f"Prof-{k}",
        )
        await db_superuser.execute(
            "insert into public.services (id, tenant_id, name, duration_minutes, price) values ($1, $2, $3, 30, 10)",
            d["servicio"],
            d["tenant"],
            f"Servicio-{k}",
        )
        await db_superuser.execute(
            "insert into public.end_customers (id, tenant_id, channel, channel_user_id, name) "
            "values ($1, $2, 'telegram', $3, $4)",
            d["cliente"],
            d["tenant"],
            f"u-{k}",
            f"Cliente-{k}",
        )
        await db_superuser.execute(
            "insert into public.booking_packs (id, tenant_id, end_customer_id, service_id, total_sessions, origin) "
            "values ($1, $2, $3, $4, 5, 'manual_grant')",
            d["pack"],
            d["tenant"],
            d["cliente"],
            d["servicio"],
        )
        inicio = DESDE + timedelta(days=1, hours=10)
        await db_superuser.execute(
            "insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, "
            "time_range, status) values ($1, $2, $3, $4, $5, tstzrange($6, $7), 'confirmed')",
            d["booking"],
            d["tenant"],
            d["cliente"],
            d["servicio"],
            d["prof"],
            inicio,
            inicio + timedelta(minutes=30),
        )
    await db_superuser.execute(
        "insert into public.tenant_users (tenant_id, auth_user_id, is_owner) values ($1, $2, true)",
        datos["a"]["tenant"],
        staff_a,
    )
    try:
        yield datos
    finally:
        ids = [datos["a"]["tenant"], datos["b"]["tenant"]]
        secretos = await db_superuser.fetch(
            "select secret_id from public.tenant_integrations where tenant_id = any($1::uuid[]) "
            "and secret_id is not null",
            ids,
        )
        async with db_superuser.transaction():
            # Limpieza sin triggers (audit_log solo-agregar, último owner): vale solo dentro de esta transacción
            await db_superuser.execute("set local session_replication_role = replica")
            for tabla in (
                "bookings",
                "booking_packs",
                "professionals",
                "services",
                "end_customers",
                "audit_log",
                "tenant_integrations",
                "tenant_users",
            ):
                await db_superuser.execute(f"delete from public.{tabla} where tenant_id = any($1::uuid[])", ids)
            await db_superuser.execute("delete from public.audit_log where actor_id = $1", admin)
            await db_superuser.execute(
                "delete from vault.secrets where id = any($1::uuid[])", [s["secret_id"] for s in secretos]
            )
            await db_superuser.execute("delete from public.tenants where id = any($1::uuid[])", ids)
            await db_superuser.execute("delete from public.platform_admins where auth_user_id = $1", admin)
            await db_superuser.execute("delete from auth.users where id = any($1::uuid[])", [admin, staff_a])


@pytest.fixture
async def app(conectar_como):
    admin = await conectar_como("platform_admin")
    readonly = await conectar_como("platform_admin_readonly")
    app, _, _ = app_de_prueba(pool=_PoolDeUnaConexion(admin), pool_readonly=_PoolDeUnaConexion(readonly))
    return app


def _ruta(d, vista):
    return f"/tenants/{d['tenant']}/impersonar/{vista}"


async def _todas_las_vistas(c, d, cabeceras):
    respuestas = {
        "bookings": await c.get(f"{_ruta(d, 'bookings')}?desde={F_DESDE}&hasta={F_HASTA}", headers=cabeceras),
        "booking": await c.get(_ruta(d, f"bookings/{d['booking']}"), headers=cabeceras),
        "clientes": await c.get(_ruta(d, "clientes"), headers=cabeceras),
        "servicios": await c.get(f"{_ruta(d, 'servicios')}?incluir_inactivos=true", headers=cabeceras),
        "profesionales": await c.get(_ruta(d, "profesionales"), headers=cabeceras),
        "packs": await c.get(_ruta(d, "packs"), headers=cabeceras),
    }
    for nombre, r in respuestas.items():
        assert r.status_code == 200, (nombre, r.text)
    return {n: r.json() for n, r in respuestas.items()}


async def test_la_impersonacion_de_a_nunca_devuelve_datos_de_b(app, escenario):
    a, b = escenario["a"], escenario["b"]
    cabeceras = bearer(user_id=escenario["admin"])
    async with http(app) as c:
        de_a = await _todas_las_vistas(c, a, cabeceras)
        de_b = await _todas_las_vistas(c, b, cabeceras)
        # el booking de B pedido por la ruta de A no existe para A
        cruzado = await c.get(_ruta(a, f"bookings/{b['booking']}"), headers=cabeceras)
    assert cruzado.status_code == 404

    for nombre, cuerpo in de_a.items():
        filas = [cuerpo] if isinstance(cuerpo, dict) else cuerpo
        assert filas, nombre
        assert {f["tenant_id"] for f in filas} == {str(a["tenant"])}, nombre
    texto_a = str(de_a)
    assert "Cliente-a" in texto_a and "Servicio-a" in texto_a and "Prof-a" in texto_a
    assert "Cliente-b" not in texto_a and "Servicio-b" not in texto_a and "Prof-b" not in texto_a
    for propio in b.values():
        assert str(propio) not in texto_a
    for nombre, cuerpo in de_b.items():
        filas = [cuerpo] if isinstance(cuerpo, dict) else cuerpo
        assert {f["tenant_id"] for f in filas} == {str(b["tenant"])}, nombre


async def test_cada_vista_deja_audit_de_platform_admin_que_el_tenant_no_ve(app, escenario, db_superuser, conectar_como):
    a, admin = escenario["a"], escenario["admin"]
    async with http(app) as c:
        await _todas_las_vistas(c, a, bearer(user_id=admin))

    filas = await db_superuser.fetch(
        "select actor_type, actor_id, entity, entity_id, metadata::text as m from public.audit_log "
        "where tenant_id = $1 and action = 'impersonate_view'",
        a["tenant"],
    )
    assert len(filas) == 6
    assert {(f["actor_type"], f["actor_id"], f["entity"], f["entity_id"]) for f in filas} == {
        ("platform_admin", admin, "tenants", a["tenant"])
    }
    assert not await db_superuser.fetchval(
        "select count(*) from public.audit_log where tenant_id = $1 and action = 'impersonate_view'",
        escenario["b"]["tenant"],
    )

    # dashboard_service ya no tiene SELECT sobre audit_log: el staff del tenant no las ve (ni ninguna otra fila)
    dashboard = await conectar_como("dashboard_service")
    async with tenant_tx(_PoolDeUnaConexion(dashboard), a["tenant"], escenario["staff_a"]) as conn:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.fetchval("select count(*) from public.audit_log")

    # ...pero Franco sí las ve en el visor del admin-backend
    async with http(app) as c:
        r = await c.get(
            f"/tenants/{a['tenant']}/audit-log?action=impersonate_view&actor_type=platform_admin",
            headers=bearer(user_id=admin),
        )
    assert r.status_code == 200 and len(r.json()) == 6


async def test_tenant_inexistente_404_sin_audit(app, escenario, db_superuser):
    inexistente = uuid.uuid4()
    async with http(app) as c:
        r = await c.get(f"/tenants/{inexistente}/impersonar/servicios", headers=bearer(user_id=escenario["admin"]))
    assert r.status_code == 404
    assert not await db_superuser.fetchval("select count(*) from public.audit_log where tenant_id = $1", inexistente)


async def test_cada_ruta_fija_en_la_transaccion_el_tenant_de_la_ruta(app, escenario, monkeypatch):
    vistos = []

    async def servicios_que_lee_el_contexto(conn, *, tenant_id, **kw):
        vistos.append((tenant_id, await conn.fetchval("select current_setting('app.current_tenant_id')")))
        return []

    monkeypatch.setattr(tq, "listar_servicios", servicios_que_lee_el_contexto)
    a, b = escenario["a"], escenario["b"]
    async with http(app) as c:
        for t in (a, b):
            r = await c.get(_ruta(t, "servicios"), headers=bearer(user_id=escenario["admin"]))
            assert r.status_code == 200
    assert vistos == [(a["tenant"], str(a["tenant"])), (b["tenant"], str(b["tenant"]))]


async def test_staff_y_no_admin_no_entran_a_la_impersonacion(app, escenario, db_superuser):
    a = escenario["a"]
    cabeceras = bearer(user_id=escenario["staff_a"], tenant_id=a["tenant"], is_owner=True, is_platform_admin=False)
    async with http(app) as c:
        r = await c.get(_ruta(a, "servicios"), headers=cabeceras)
    assert r.status_code == 403
    assert not await db_superuser.fetchval(
        "select count(*) from public.audit_log where tenant_id = $1 and action = 'impersonate_view'", a["tenant"]
    )


async def test_cualquier_escritura_durante_la_impersonacion_falla_en_la_base(
    app, escenario, db_superuser, conectar_como, monkeypatch
):
    a = escenario["a"]
    errores = (asyncpg.InsufficientPrivilegeError, asyncpg.ReadOnlySQLTransactionError)

    # 1) Forzado desde el código, dentro de la misma transacción que usa la impersonación.
    async def servicios_que_escribe(conn, *, tenant_id, **kw):
        await conn.execute("update public.services set name = 'hackeado' where tenant_id = $1", tenant_id)
        return []

    monkeypatch.setattr(tq, "listar_servicios", servicios_que_escribe)
    async with http(app) as c:
        r = await c.get(_ruta(a, "servicios"), headers=bearer(user_id=escenario["admin"]))
    # La base rechaza la escritura (la excepcion llega al 500 generico, sin detalle) y no cambia nada.
    assert r.status_code == 500 and "hackeado" not in r.text
    assert await db_superuser.fetchval("select name from public.services where id = $1", a["servicio"]) == "Servicio-a"

    # 2) Con el rol de solo lectura, ninguna escritura prospera, ni siquiera fuera de un BEGIN READ ONLY.
    readonly = await conectar_como("platform_admin_readonly")
    sentencias = (
        "update public.services set name = 'x'",
        "delete from public.bookings",
        "insert into public.tenants (name, slug) values ('x', 'it-imp-x')",
        "update public.end_customers set name = 'x'",
        "select public.write_audit_log('impersonate_view')",
    )
    for sql in sentencias:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await readonly.execute(sql)
    # 3) Y dentro de readonly_tx.
    for sql in sentencias[:4]:
        with pytest.raises(errores):
            async with readonly_tx(_PoolDeUnaConexion(readonly), a["tenant"], escenario["admin"]) as conn:
                await conn.execute(sql)

    nombres = await db_superuser.fetch(
        "select name from public.services where tenant_id = any($1::uuid[])", [a["tenant"], escenario["b"]["tenant"]]
    )
    assert {n["name"] for n in nombres} == {"Servicio-a", "Servicio-b"}


async def test_visor_de_audit_log_de_un_tenant_no_mezcla_tenants(app, escenario):
    a, b, admin = escenario["a"], escenario["b"], escenario["admin"]
    async with http(app) as c:
        await c.get(_ruta(a, "servicios"), headers=bearer(user_id=admin))
        await c.get(_ruta(b, "servicios"), headers=bearer(user_id=admin))
        ra = await c.get(f"/tenants/{a['tenant']}/audit-log", headers=bearer(user_id=admin))
        sin_tenant = await c.get(f"/tenants/{uuid.uuid4()}/audit-log", headers=bearer(user_id=admin))
    assert ra.status_code == 200 and ra.json()
    assert {e["tenant_id"] for e in ra.json()} == {str(a["tenant"])}
    assert sin_tenant.status_code == 404


async def test_visor_de_notificaciones_con_problemas(app, escenario, db_superuser):
    a, admin = escenario["a"], escenario["admin"]
    viejo = datetime.now(timezone.utc) - timedelta(hours=3)  # noqa: UP017
    await db_superuser.execute(
        "insert into public.notifications_log (tenant_id, end_customer_id, channel, template, status, retry_count, "
        "last_error, created_at) values ($1, $2, 'telegram', 'booking_approved', 'failed', 3, 'timeout', now())",
        a["tenant"],
        a["cliente"],
    )
    await db_superuser.execute(
        "insert into public.notifications_log (tenant_id, end_customer_id, channel, template, status, retry_count, "
        "created_at) values ($1, $2, 'telegram', 'booking_approved', 'pending', 0, $3)",
        a["tenant"],
        a["cliente"],
        viejo,
    )
    await db_superuser.execute(
        "insert into public.notifications_log (tenant_id, end_customer_id, channel, template, status, retry_count) "
        "values ($1, $2, 'telegram', 'booking_approved', 'pending', 0)",
        a["tenant"],
        a["cliente"],
    )
    async with http(app) as c:
        r = await c.get("/notificaciones/problemas?limit=500", headers=bearer(user_id=admin))
    assert r.status_code == 200
    propias = [n for n in r.json()["notificaciones"] if n["tenant_id"] == str(a["tenant"])]
    assert sorted(n["status"] for n in propias) == ["failed", "pending"]  # el pending reciente no cuenta


async def test_cargar_integracion_de_telegram_guarda_y_no_devuelve_el_secret(app, escenario, db_superuser):
    a, admin = escenario["a"], escenario["admin"]
    secreto = json.dumps({"bot_token": "987654:ABC-" + uuid.uuid4().hex, "webhook_secret": uuid.uuid4().hex})
    async with http(app) as c:
        r = await c.put(
            f"/tenants/{a['tenant']}/integraciones/telegram_bot",
            json={"config": {"bot_username": "mi_bot"}, "secret": secreto},
            headers=bearer(user_id=admin),
        )
    assert r.status_code == 200, r.text
    assert secreto not in r.text
    cuerpo = r.json()
    assert cuerpo["integration_type"] == "telegram_bot" and cuerpo["tenant_id"] == str(a["tenant"])
    fila = await db_superuser.fetchrow(
        "select config::text as c, secret_id from public.tenant_integrations where id = $1",
        uuid.UUID(cuerpo["integration_id"]),
    )
    assert secreto not in fila["c"] and fila["secret_id"] is not None
    auditoria = await db_superuser.fetchval(
        "select metadata::text from public.audit_log where tenant_id = $1 and action = 'store_tenant_integration'",
        a["tenant"],
    )
    assert secreto not in (auditoria or "")
