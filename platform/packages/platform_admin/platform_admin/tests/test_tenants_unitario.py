"""Tests unitarios de platform_admin: conexión y cliente de Auth Admin falsos, sin base ni red."""

import uuid
from contextlib import asynccontextmanager

import pytest

from platform_admin import (
    DatosTenantInvalidos,
    ErrorInvitacion,
    InvitacionNoPendiente,
    OwnerNoDisponible,
    PurgaEnCurso,
    SlugEnUso,
    TenantNoEncontrado,
    TransicionInvalida,
    create_tenant,
    dar_de_baja,
    levantar_suspension,
    listar_tenants,
    reactivar,
    reenviar_invitacion,
    suspender,
)

ACTOR = uuid.UUID("22222222-2222-2222-2222-222222222222")
TENANT = uuid.UUID("33333333-3333-3333-3333-333333333333")
OWNER = uuid.UUID("44444444-4444-4444-4444-444444444444")
USUARIO_AUTH = uuid.UUID("55555555-5555-5555-5555-555555555555")
EMAIL = f"duenia-{uuid.uuid4().hex[:8]}@prueba.invalid"
NOMBRE = "Lucía Gómez"
EMAIL_NUEVO = f"nuevo-{uuid.uuid4().hex[:8]}@prueba.invalid"


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
        self.nombres: list[str | None] = []

    async def invitar(self, email, nombre=None):
        self.invitados.append(email)
        self.nombres.append(nombre)
        if self.falla:
            raise ErrorInvitacion("red caída")
        return USUARIO_AUTH


def _conn_creacion(respuestas_extra=None, **kw):
    return ConexionFalsa(
        respuestas={
            "insert into public.tenants": TENANT,
            "insert into public.tenant_users": OWNER,
            **(respuestas_extra or {}),
        },
        **kw,
    )


DATOS = {"name": "Estética Sol", "slug": "estetica-sol"}


# --- create_tenant ---------------------------------------------------------------------------------------


async def test_create_tenant_ok_crea_tenant_y_owner_y_invita_en_una_sola_transaccion():
    conn = _conn_creacion()
    pool, cliente = PoolFalso(conn), ClienteAuthFalso()

    res = await create_tenant(pool, cliente, DATOS, EMAIL, owner_nombre=NOMBRE, actor_id=ACTOR)

    assert res.estado == "ok"
    assert res.tenant_id == TENANT and res.owner_id == OWNER
    s1 = conn.sqls()
    assert s1[0] == "BEGIN" and s1[-1] == "COMMIT" and s1.count("COMMIT") == 1
    assert any("insert into public.tenants" in s for s in s1)
    insert_owner = [c for c in conn.comandos if "insert into public.tenant_users" in c[0]][0]
    assert "true" in insert_owner[0].lower() and insert_owner[1][0] == TENANT
    assert cliente.invitados == [EMAIL]
    assert any("update public.tenant_users" in s and "auth_user_id" in s for s in s1)
    assert [a[0] for a in conn.auditorias()] == ["tenant_create", "tenant_invite_sent"]


async def test_create_tenant_invite_falla_deja_tenant_y_owner_y_devuelve_invite_pendiente():
    conn = _conn_creacion()
    pool, cliente = PoolFalso(conn), ClienteAuthFalso(falla=True)

    res = await create_tenant(pool, cliente, DATOS, EMAIL, owner_nombre=NOMBRE, actor_id=ACTOR)

    assert res.estado == "invite_pendiente"
    assert res.tenant_id == TENANT
    assert conn.sqls()[-1] == "COMMIT"  # no se revierte la creación
    assert not any("update public.tenant_users set auth_user_id" in s for s in conn.sqls())
    assert [a[0] for a in conn.auditorias()] == ["tenant_create", "tenant_invite_failed"]


def _guardados(conn):
    return [c for c in conn.comandos if "pending_invite_email" in c[0] and "update" in c[0]]


async def test_create_tenant_ok_no_guarda_el_email_ni_lo_manda_a_la_base():
    conn = _conn_creacion()
    await create_tenant(PoolFalso(conn), ClienteAuthFalso(), DATOS, EMAIL, owner_nombre=NOMBRE, actor_id=ACTOR)
    comandos_sin_consulta = [c for c in conn.comandos if "pending_invite_email" not in c[0]]
    assert EMAIL not in repr(comandos_sin_consulta)


async def test_create_tenant_invite_falla_guarda_el_email_pendiente_pero_no_lo_audita():
    conn = _conn_creacion()
    cliente = ClienteAuthFalso(falla=True)
    await create_tenant(PoolFalso(conn), cliente, DATOS, EMAIL, owner_nombre=NOMBRE, actor_id=ACTOR)
    (guardado,) = _guardados(conn)
    assert guardado[1] == (OWNER, EMAIL, NOMBRE) and "auth_user_id is null" in guardado[0]
    assert EMAIL not in repr(conn.auditorias())
    assert conn.sqls()[-1] == "COMMIT"


class Unica(Exception):
    sqlstate = "23505"


async def test_create_tenant_con_invitacion_pendiente_en_otro_tenant_es_owner_no_disponible_y_no_crea_nada():
    conn = _conn_creacion(respuestas_extra={"pending_invite_email) = lower": True})
    cliente = ClienteAuthFalso()
    with pytest.raises(OwnerNoDisponible) as e:
        await create_tenant(PoolFalso(conn), cliente, DATOS, EMAIL, owner_nombre=NOMBRE, actor_id=ACTOR)
    assert conn.sqls()[-1] == "ROLLBACK"
    assert not any("insert into" in s for s in conn.sqls())
    assert cliente.invitados == []
    assert EMAIL not in str(e.value)


async def test_create_tenant_con_cuenta_ya_vinculada_en_otro_tenant_revierte_todo_con_owner_no_disponible():
    conn = _conn_creacion(falla_en=("update public.tenant_users set auth_user_id", Unica("duplicate key")))
    cliente = ClienteAuthFalso()
    with pytest.raises(OwnerNoDisponible):
        await create_tenant(PoolFalso(conn), cliente, DATOS, EMAIL, owner_nombre=NOMBRE, actor_id=ACTOR)
    assert conn.sqls()[-1] == "ROLLBACK" and "COMMIT" not in conn.sqls()
    assert "tenant_invite_sent" not in [a[0] for a in conn.auditorias()]


async def test_ambos_casos_de_owner_no_disponible_dan_el_mismo_error():
    pendiente = _conn_creacion(respuestas_extra={"pending_invite_email) = lower": True})
    vinculada = _conn_creacion(falla_en=("update public.tenant_users set auth_user_id", Unica("duplicate key")))
    errores = []
    for conn in (pendiente, vinculada):
        with pytest.raises(OwnerNoDisponible) as e:
            await create_tenant(PoolFalso(conn), ClienteAuthFalso(), DATOS, EMAIL, owner_nombre=NOMBRE, actor_id=ACTOR)
        errores.append((type(e.value), str(e.value)))
    assert errores[0] == errores[1]


async def test_create_tenant_otro_error_de_base_al_vincular_no_se_disfraza():
    conn = _conn_creacion(falla_en=("update public.tenant_users set auth_user_id", RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        await create_tenant(PoolFalso(conn), ClienteAuthFalso(), DATOS, EMAIL, owner_nombre=NOMBRE, actor_id=ACTOR)


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
        await create_tenant(pool, cliente, datos, EMAIL, owner_nombre=NOMBRE, actor_id=ACTOR)
    assert pool.usadas == [] and cliente.invitados == []


@pytest.mark.parametrize("email", ["", "sin-arroba", "a@b", "a b@c.com", None])
async def test_create_tenant_email_invalido(email):
    pool, cliente = PoolFalso(), ClienteAuthFalso()
    with pytest.raises(DatosTenantInvalidos):
        await create_tenant(pool, cliente, DATOS, email, owner_nombre=NOMBRE, actor_id=ACTOR)
    assert pool.usadas == [] and cliente.invitados == []


async def test_create_tenant_slug_duplicado_es_error_de_dominio_y_no_invita():
    class Unica(Exception):
        sqlstate = "23505"

    paso1 = ConexionFalsa(falla_en=("insert into public.tenants", Unica("duplicate key")))
    cliente = ClienteAuthFalso()
    with pytest.raises(SlugEnUso):
        await create_tenant(PoolFalso(paso1), cliente, DATOS, EMAIL, owner_nombre=NOMBRE, actor_id=ACTOR)
    assert paso1.sqls()[-1] == "ROLLBACK"
    assert cliente.invitados == []


# --- reenviar_invitacion ---------------------------------------------------------------------------------


def _owner(pendiente=EMAIL, auth=None, nombre=None):
    return {
        "from public.tenant_users": {
            "id": OWNER,
            "auth_user_id": auth,
            "pending_invite_email": pendiente,
            "pending_invite_name": nombre,
        }
    }


async def test_reenviar_invitacion_usa_el_email_guardado_sin_que_se_lo_pasen():
    conn1 = ConexionFalsa(respuestas=_owner())
    conn2 = ConexionFalsa()
    cliente = ClienteAuthFalso()
    res = await reenviar_invitacion(PoolFalso(conn1, conn2), cliente, TENANT, actor_id=ACTOR)
    assert res.estado == "ok"
    assert cliente.invitados == [EMAIL]
    assert [a[0] for a in conn2.auditorias()] == ["tenant_invite_sent"]
    assert EMAIL not in repr(conn2.auditorias())


async def test_reenviar_invitacion_sin_email_guardado_ni_explicito_es_no_pendiente():
    cliente = ClienteAuthFalso()
    conn1 = ConexionFalsa(respuestas=_owner(pendiente=None))
    with pytest.raises(InvitacionNoPendiente):
        await reenviar_invitacion(PoolFalso(conn1), cliente, TENANT, actor_id=ACTOR)
    assert cliente.invitados == []


async def test_reenviar_invitacion_con_email_explicito_reemplaza_al_guardado():
    conn1 = ConexionFalsa(respuestas=_owner())
    conn2 = ConexionFalsa()
    cliente = ClienteAuthFalso(falla=True)
    res = await reenviar_invitacion(PoolFalso(conn1, conn2), cliente, TENANT, EMAIL_NUEVO, actor_id=ACTOR)
    assert res.estado == "invite_pendiente"
    assert cliente.invitados == [EMAIL_NUEVO]
    (guardado,) = _guardados(conn2)
    assert guardado[1][:2] == (OWNER, EMAIL_NUEVO)
    assert EMAIL_NUEVO not in repr(conn2.auditorias()) and EMAIL not in repr(conn2.comandos)


async def test_reenviar_invitacion_email_explicito_invalido():
    pool = PoolFalso()
    with pytest.raises(DatosTenantInvalidos):
        await reenviar_invitacion(pool, ClienteAuthFalso(), TENANT, "sin-arroba", actor_id=ACTOR)
    assert pool.usadas == []


async def test_reenviar_invitacion_si_falla_sigue_pendiente_y_audita_sin_email():
    conn1 = ConexionFalsa(respuestas=_owner())
    conn2 = ConexionFalsa()
    res = await reenviar_invitacion(PoolFalso(conn1, conn2), ClienteAuthFalso(falla=True), TENANT, actor_id=ACTOR)
    assert res.estado == "invite_pendiente"
    assert [a[0] for a in conn2.auditorias()] == ["tenant_invite_failed"]
    assert EMAIL not in repr(conn2.auditorias())


async def test_reenviar_invitacion_owner_ya_vinculado_es_error_de_dominio():
    conn1 = ConexionFalsa(respuestas=_owner(pendiente=None, auth=USUARIO_AUTH))
    cliente = ClienteAuthFalso()
    with pytest.raises(InvitacionNoPendiente):
        await reenviar_invitacion(PoolFalso(conn1), cliente, TENANT, EMAIL_NUEVO, actor_id=ACTOR)
    assert cliente.invitados == []


async def test_reenviar_invitacion_tenant_inexistente():
    conn1 = ConexionFalsa()
    with pytest.raises(TenantNoEncontrado):
        await reenviar_invitacion(PoolFalso(conn1), ClienteAuthFalso(), TENANT, actor_id=ACTOR)


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

EN_PURGA = [suspender, levantar_suspension, dar_de_baja, reactivar]


@pytest.mark.parametrize(("accion", "desde"), INVALIDAS)
async def test_transicion_invalida_es_error_de_dominio_sin_escribir(accion, desde):
    conn = ConexionFalsa(respuestas={"select status": desde})
    with pytest.raises(TransicionInvalida) as exc:
        await accion(PoolFalso(conn), TENANT, actor_id=ACTOR)
    assert exc.value.desde == desde
    assert not any("update public.tenants" in s for s in conn.sqls())
    assert conn.auditorias() == []
    assert conn.sqls()[-1] == "ROLLBACK"


@pytest.mark.parametrize("accion", EN_PURGA)
async def test_un_tenant_en_purging_no_admite_ninguna_accion_y_el_error_es_purga_en_curso(accion):
    conn = ConexionFalsa(respuestas={"select status": "purging"})
    with pytest.raises(PurgaEnCurso) as exc:
        await accion(PoolFalso(conn), TENANT, actor_id=ACTOR)
    assert isinstance(exc.value, TransicionInvalida) and exc.value.desde == "purging"
    assert "purga" in str(exc.value)
    assert not any("update public.tenants" in s for s in conn.sqls())
    assert conn.auditorias() == [] and conn.sqls()[-1] == "ROLLBACK"


async def test_listar_tenants_acepta_el_estado_purging():
    conn = ConexionFalsa()
    assert await listar_tenants(PoolFalso(conn), status="purging") == []
    assert [c[1] for c in conn.comandos if "where t.status = $1" in c[0]] == [("purging",)]
    with pytest.raises(DatosTenantInvalidos):
        await listar_tenants(PoolFalso(ConexionFalsa()), status="otro")


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


# --- nombre del owner (user metadata full_name) ------------------------------------------------------------


async def test_create_tenant_envia_el_nombre_limpio_al_cliente_y_no_lo_guarda_ni_lo_audita():
    conn = _conn_creacion()
    cliente = ClienteAuthFalso()
    await create_tenant(PoolFalso(conn), cliente, DATOS, EMAIL, owner_nombre="  Lucía   Gómez ", actor_id=ACTOR)
    assert cliente.nombres == [NOMBRE]
    assert "Lucía" not in repr(conn.comandos)  # invite ok: el nombre no se guarda
    assert "Lucía" not in repr(conn.auditorias())


async def test_create_tenant_invite_falla_guarda_el_nombre_pendiente_pero_no_lo_audita():
    conn = _conn_creacion()
    await create_tenant(
        PoolFalso(conn),
        ClienteAuthFalso(falla=True),
        DATOS,
        EMAIL,
        owner_nombre="  Lucía   Gómez ",
        actor_id=ACTOR,
    )
    (guardado,) = _guardados(conn)
    assert "pending_invite_name" in guardado[0] and guardado[1][2] == NOMBRE
    assert "Lucía" not in repr(conn.auditorias())


async def test_reenviar_invitacion_usa_el_nombre_guardado_si_no_se_pasa_uno():
    cliente = ClienteAuthFalso()
    await reenviar_invitacion(
        PoolFalso(ConexionFalsa(respuestas=_owner(nombre=NOMBRE)), ConexionFalsa()), cliente, TENANT, actor_id=ACTOR
    )
    assert cliente.nombres == [NOMBRE]


async def test_reenviar_invitacion_nombre_nuevo_reemplaza_al_guardado_y_si_falla_queda_guardado():
    cliente = ClienteAuthFalso(falla=True)
    conn2 = ConexionFalsa()
    res = await reenviar_invitacion(
        PoolFalso(ConexionFalsa(respuestas=_owner(nombre="Otro Nombre")), conn2),
        cliente,
        TENANT,
        owner_nombre=NOMBRE,
        actor_id=ACTOR,
    )
    assert res.estado == "invite_pendiente"
    assert cliente.nombres == [NOMBRE]
    (guardado,) = _guardados(conn2)
    assert guardado[1][2] == NOMBRE
    assert "Lucía" not in repr(conn2.auditorias())


async def test_reenviar_invitacion_fallida_sin_nombre_nuevo_conserva_el_guardado():
    conn2 = ConexionFalsa()
    await reenviar_invitacion(
        PoolFalso(ConexionFalsa(respuestas=_owner(nombre=NOMBRE)), conn2),
        ClienteAuthFalso(falla=True),
        TENANT,
        actor_id=ACTOR,
    )
    (guardado,) = _guardados(conn2)
    assert guardado[1][2] == NOMBRE


NOMBRES_INVALIDOS = [None, "", "   ", 5, "x" * 121, "con" + chr(0) + "nulo"]


@pytest.mark.parametrize("nombre", NOMBRES_INVALIDOS, ids=repr)
async def test_create_tenant_nombre_invalido_no_toca_la_base_ni_invita_ni_repite_el_valor(nombre):
    pool, cliente = PoolFalso(), ClienteAuthFalso()
    with pytest.raises(DatosTenantInvalidos) as e:
        await create_tenant(pool, cliente, DATOS, EMAIL, owner_nombre=nombre, actor_id=ACTOR)
    assert pool.usadas == [] and cliente.invitados == []
    if isinstance(nombre, str) and len(nombre.strip()) > 3:
        assert nombre.strip() not in str(e.value)


async def test_reenviar_invitacion_envia_el_nombre_si_se_pasa_y_si_no_invita_sin_nombre():
    cliente = ClienteAuthFalso()
    await reenviar_invitacion(
        PoolFalso(ConexionFalsa(respuestas=_owner()), ConexionFalsa()),
        cliente,
        TENANT,
        owner_nombre=NOMBRE,
        actor_id=ACTOR,
    )
    await reenviar_invitacion(
        PoolFalso(ConexionFalsa(respuestas=_owner()), ConexionFalsa()), cliente, TENANT, actor_id=ACTOR
    )
    assert cliente.nombres == [NOMBRE, None]


async def test_reenviar_invitacion_nombre_invalido_no_toca_la_base():
    pool, cliente = PoolFalso(), ClienteAuthFalso()
    with pytest.raises(DatosTenantInvalidos):
        await reenviar_invitacion(pool, cliente, TENANT, owner_nombre="   ", actor_id=ACTOR)
    assert pool.usadas == [] and cliente.invitados == []
