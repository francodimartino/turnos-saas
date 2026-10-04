"""Tests unitarios de platform_admin: conexión y cliente de Auth Admin falsos, sin base ni red."""

import uuid
from contextlib import asynccontextmanager

import pytest

from platform_admin import (
    DatosTenantInvalidos,
    ErrorInvitacion,
    InvitacionNoPendiente,
    SlugEnUso,
    TenantNoEncontrado,
    TransicionInvalida,
    create_tenant,
    dar_de_baja,
    levantar_suspension,
    reactivar,
    reenviar_invitacion,
    suspender,
)

ACTOR = uuid.UUID("22222222-2222-2222-2222-222222222222")
TENANT = uuid.UUID("33333333-3333-3333-3333-333333333333")
OWNER = uuid.UUID("44444444-4444-4444-4444-444444444444")
USUARIO_AUTH = uuid.UUID("55555555-5555-5555-5555-555555555555")
EMAIL = "duenia@example.com"


class ConexionFalsa:
    """Responde por fragmento de SQL; guarda todo lo ejecutado."""

    def __init__(self, respuestas=None, falla_en=None):
        self.respuestas = respuestas or {}
        self.falla_en = falla_en  # (fragmento, excepción)
        self.comandos: list[tuple[str, tuple]] = []

    def _responder(self, sql, args):
        self.comandos.append((sql, args))
        if self.falla_en and self.falla_en[0] in sql:
            raise self.falla_en[1]
        if "write_audit_log" in sql:
            return uuid.uuid4()
        for fragmento, valor in self.respuestas.items():
            if fragmento in sql:
                return valor
        return None

    async def execute(self, sql, *args):
        self._responder(sql, args)
        return "OK"

    async def fetchval(self, sql, *args):
        return self._responder(sql, args)

    async def fetchrow(self, sql, *args):
        return self._responder(sql, args)

    async def fetch(self, sql, *args):
        return self._responder(sql, args) or []

    def sqls(self):
        return [c[0] for c in self.comandos]

    def auditorias(self):
        return [c[1] for c in self.comandos if "write_audit_log" in c[0]]


class PoolFalso:
    """Cada `acquire` devuelve la siguiente conexión de la lista (una por transacción)."""

    def __init__(self, *conexiones):
        self.conexiones = list(conexiones)
        self.usadas: list[ConexionFalsa] = []

    @asynccontextmanager
    async def acquire(self):
        conn = self.conexiones.pop(0)
        self.usadas.append(conn)
        yield conn


class ClienteAuthFalso:
    def __init__(self, falla=False):
        self.falla = falla
        self.invitados: list[str] = []

    async def invitar(self, email):
        self.invitados.append(email)
        if self.falla:
            raise ErrorInvitacion("red caída")
        return USUARIO_AUTH


def _conn_creacion(**kw):
    return ConexionFalsa(
        respuestas={"insert into public.tenants": TENANT, "insert into public.tenant_users": OWNER}, **kw
    )


DATOS = {"name": "Estética Sol", "slug": "estetica-sol"}


# --- create_tenant ---------------------------------------------------------------------------------------


async def test_create_tenant_ok_crea_tenant_y_owner_en_una_transaccion_y_luego_invita():
    paso1, paso2 = _conn_creacion(), ConexionFalsa()
    pool, cliente = PoolFalso(paso1, paso2), ClienteAuthFalso()

    res = await create_tenant(pool, cliente, DATOS, EMAIL, actor_id=ACTOR)

    assert res.estado == "ok"
    assert res.tenant_id == TENANT and res.owner_id == OWNER
    s1 = paso1.sqls()
    assert s1[0] == "BEGIN" and s1[-1] == "COMMIT"
    assert any("insert into public.tenants" in s for s in s1)
    insert_owner = [c for c in paso1.comandos if "insert into public.tenant_users" in c[0]][0]
    assert "true" in insert_owner[0].lower() and insert_owner[1][0] == TENANT
    assert cliente.invitados == [EMAIL]
    assert any("update public.tenant_users" in s and "auth_user_id" in s for s in paso2.sqls())
    acciones = [a[0] for a in paso1.auditorias() + paso2.auditorias()]
    assert acciones == ["tenant_create", "tenant_invite_sent"]


async def test_create_tenant_invite_falla_deja_tenant_y_owner_y_devuelve_invite_pendiente():
    paso1, paso2 = _conn_creacion(), ConexionFalsa()
    pool, cliente = PoolFalso(paso1, paso2), ClienteAuthFalso(falla=True)

    res = await create_tenant(pool, cliente, DATOS, EMAIL, actor_id=ACTOR)

    assert res.estado == "invite_pendiente"
    assert res.tenant_id == TENANT
    assert paso1.sqls()[-1] == "COMMIT"  # no se revierte la creación
    assert not any("update public.tenant_users" in s for s in paso2.sqls())
    assert [a[0] for a in paso2.auditorias()] == ["tenant_invite_failed"]


async def test_create_tenant_el_email_nunca_va_a_la_base_ni_al_audit():
    paso1, paso2 = _conn_creacion(), ConexionFalsa()
    await create_tenant(PoolFalso(paso1, paso2), ClienteAuthFalso(), DATOS, EMAIL, actor_id=ACTOR)
    for conn in (paso1, paso2):
        assert EMAIL not in repr(conn.comandos)


async def test_create_tenant_el_error_del_cliente_no_se_filtra_al_audit():
    paso1, paso2 = _conn_creacion(), ConexionFalsa()
    await create_tenant(PoolFalso(paso1, paso2), ClienteAuthFalso(falla=True), DATOS, EMAIL, actor_id=ACTOR)
    assert EMAIL not in repr(paso2.comandos)


@pytest.mark.parametrize(
    "datos",
    [
        {"slug": "x"},
        {"name": "  ", "slug": "x"},
        {"name": "A"},
        {"name": "A", "slug": "Con Espacios"},
        {"name": "A", "slug": "-malo"},
        {"name": "A", "slug": "ok", "status": "purged"},
        {"name": "A", "slug": "ok", "plan": "otro"},
        {"name": "A", "slug": "ok", "settings": "no-dict"},
        "no-es-dict",
    ],
)
async def test_create_tenant_datos_invalidos_no_toca_la_base_ni_invita(datos):
    pool, cliente = PoolFalso(), ClienteAuthFalso()
    with pytest.raises(DatosTenantInvalidos):
        await create_tenant(pool, cliente, datos, EMAIL, actor_id=ACTOR)
    assert pool.usadas == [] and cliente.invitados == []


@pytest.mark.parametrize("email", ["", "sin-arroba", "a@b", "a b@c.com", None])
async def test_create_tenant_email_invalido(email):
    pool, cliente = PoolFalso(), ClienteAuthFalso()
    with pytest.raises(DatosTenantInvalidos):
        await create_tenant(pool, cliente, DATOS, email, actor_id=ACTOR)
    assert pool.usadas == [] and cliente.invitados == []


async def test_create_tenant_slug_duplicado_es_error_de_dominio_y_no_invita():
    class Unica(Exception):
        sqlstate = "23505"

    paso1 = ConexionFalsa(falla_en=("insert into public.tenants", Unica("duplicate key")))
    cliente = ClienteAuthFalso()
    with pytest.raises(SlugEnUso):
        await create_tenant(PoolFalso(paso1), cliente, DATOS, EMAIL, actor_id=ACTOR)
    assert paso1.sqls()[-1] == "ROLLBACK"
    assert cliente.invitados == []


# --- reenviar_invitacion ---------------------------------------------------------------------------------


async def test_reenviar_invitacion_invita_vincula_y_audita():
    conn1 = ConexionFalsa(respuestas={"from public.tenant_users": {"id": OWNER, "auth_user_id": None}})
    conn2 = ConexionFalsa()
    cliente = ClienteAuthFalso()
    res = await reenviar_invitacion(PoolFalso(conn1, conn2), cliente, TENANT, EMAIL, actor_id=ACTOR)
    assert res.estado == "ok"
    assert cliente.invitados == [EMAIL]
    assert [a[0] for a in conn2.auditorias()] == ["tenant_invite_sent"]


async def test_reenviar_invitacion_si_falla_sigue_pendiente():
    conn1 = ConexionFalsa(respuestas={"from public.tenant_users": {"id": OWNER, "auth_user_id": None}})
    conn2 = ConexionFalsa()
    res = await reenviar_invitacion(
        PoolFalso(conn1, conn2), ClienteAuthFalso(falla=True), TENANT, EMAIL, actor_id=ACTOR
    )
    assert res.estado == "invite_pendiente"
    assert [a[0] for a in conn2.auditorias()] == ["tenant_invite_failed"]


async def test_reenviar_invitacion_owner_ya_vinculado_es_error_de_dominio():
    conn1 = ConexionFalsa(respuestas={"from public.tenant_users": {"id": OWNER, "auth_user_id": USUARIO_AUTH}})
    cliente = ClienteAuthFalso()
    with pytest.raises(InvitacionNoPendiente):
        await reenviar_invitacion(PoolFalso(conn1), cliente, TENANT, EMAIL, actor_id=ACTOR)
    assert cliente.invitados == []


async def test_reenviar_invitacion_tenant_inexistente():
    conn1 = ConexionFalsa()
    with pytest.raises(TenantNoEncontrado):
        await reenviar_invitacion(PoolFalso(conn1), ClienteAuthFalso(), TENANT, EMAIL, actor_id=ACTOR)


# --- ciclo de vida ---------------------------------------------------------------------------------------

ACCIONES = [
    (suspender, "active", "suspended", "tenant_suspend"),
    (levantar_suspension, "suspended", "active", "tenant_unsuspend"),
    (dar_de_baja, "active", "inactive", "tenant_deactivate"),
    (dar_de_baja, "suspended", "inactive", "tenant_deactivate"),
    (reactivar, "inactive", "active", "tenant_reactivate"),
]


@pytest.mark.parametrize(("accion", "desde", "hasta", "auditoria"), ACCIONES)
async def test_transicion_valida_actualiza_y_audita(accion, desde, hasta, auditoria):
    conn = ConexionFalsa(respuestas={"select status": desde})
    await accion(PoolFalso(conn), TENANT, actor_id=ACTOR)
    update = [c for c in conn.comandos if "update public.tenants" in c[0]]
    assert len(update) == 1 and update[0][1] == (TENANT, hasta)
    (aud,) = conn.auditorias()
    assert aud[0] == auditoria and aud[1] == "tenants" and aud[2] == TENANT
    assert conn.sqls()[-1] == "COMMIT"


INVALIDAS = [
    (suspender, "suspended"),
    (suspender, "inactive"),
    (suspender, "purged"),
    (levantar_suspension, "active"),
    (levantar_suspension, "inactive"),
    (dar_de_baja, "inactive"),
    (dar_de_baja, "purged"),
    (reactivar, "active"),
    (reactivar, "suspended"),
    (reactivar, "purged"),
]


@pytest.mark.parametrize(("accion", "desde"), INVALIDAS)
async def test_transicion_invalida_es_error_de_dominio_sin_escribir(accion, desde):
    conn = ConexionFalsa(respuestas={"select status": desde})
    with pytest.raises(TransicionInvalida) as exc:
        await accion(PoolFalso(conn), TENANT, actor_id=ACTOR)
    assert exc.value.desde == desde
    assert not any("update public.tenants" in s for s in conn.sqls())
    assert conn.auditorias() == []
    assert conn.sqls()[-1] == "ROLLBACK"


async def test_el_trigger_de_la_base_tambien_se_traduce_a_error_de_dominio():
    class Check(Exception):
        sqlstate = "23514"

    conn = ConexionFalsa(respuestas={"select status": "active"}, falla_en=("update public.tenants", Check("x")))
    with pytest.raises(TransicionInvalida):
        await suspender(PoolFalso(conn), TENANT, actor_id=ACTOR)


async def test_tenant_inexistente_en_ciclo_de_vida():
    with pytest.raises(TenantNoEncontrado):
        await suspender(PoolFalso(ConexionFalsa()), TENANT, actor_id=ACTOR)


async def test_otros_errores_de_base_no_se_disfrazan():
    conn = ConexionFalsa(
        respuestas={"select status": "active"}, falla_en=("update public.tenants", RuntimeError("boom"))
    )
    with pytest.raises(RuntimeError):
        await suspender(PoolFalso(conn), TENANT, actor_id=ACTOR)
