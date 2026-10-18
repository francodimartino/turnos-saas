"""`invitar_staff` con una base y un cliente de Auth Admin simulados (la base real se prueba en integración).

La base simulada aplica las escrituras solo al COMMIT y las descarta en el ROLLBACK, igual que Postgres.
"""

import uuid
from contextlib import asynccontextmanager

import pytest

from platform_auth import (
    EmailInvalido,
    ErrorInvitacion,
    NoEsOwner,
    NombreInvalido,
    PermisoDesconocido,
    StaffYaExiste,
    invitar_staff,
)
from platform_auth.sesion import SesionDashboard

PERMISOS_BASE = ["view_bookings", "approve_bookings", "edit_prices", "manage_staff"]


class _BaseFalsa:
    def __init__(self, *, es_owner=True):
        self.es_owner = es_owner
        self.tenant_users: dict[uuid.UUID, uuid.UUID] = {}  # auth_user_id -> tenant_user_id
        self.permisos: set[tuple[uuid.UUID, str]] = set()
        self.auditoria: list[tuple] = []
        self.fallar_insert_una_vez = False
        self.en_otro_tenant: set[uuid.UUID] = set()  # cuentas ya vinculadas a un tenant ajeno
        self.sql: list[str] = []


class _Conn:
    def __init__(self, base: _BaseFalsa):
        self.base = base
        self._pendiente: list = []

    async def execute(self, sql, *args):
        self.base.sql.append(sql)
        if sql == "COMMIT":
            for aplicar in self._pendiente:
                aplicar()
        if sql in ("COMMIT", "ROLLBACK"):
            self._pendiente = []
        if "insert into public.tenant_user_permissions" in sql:
            tu_id, claves = args[0], args[1]
            self._pendiente.append(lambda: self.base.permisos.update((tu_id, c) for c in claves))

    async def fetch(self, sql, *args):
        self.base.sql.append(sql)
        if "from public.permissions" in sql:
            return [{"key": k} for k in PERMISOS_BASE]
        raise AssertionError(f"fetch inesperado: {sql}")

    async def fetchrow(self, sql, *args):
        self.base.sql.append(sql)
        if "insert into public.tenant_users" in sql:
            if self.base.fallar_insert_una_vez:
                self.base.fallar_insert_una_vez = False
                raise RuntimeError("caída de la base")
            auth_id = args[1]
            if auth_id in self.base.tenant_users or auth_id in self.base.en_otro_tenant:
                return None  # on conflict do nothing
            nuevo = uuid.uuid4()
            self._pendiente.append(lambda: self.base.tenant_users.__setitem__(auth_id, nuevo))
            return {"id": nuevo}
        if "from public.tenant_users" in sql and "auth_user_id = $2" in sql and "is_owner" in sql:
            return {"is_owner": self.base.es_owner, "is_active": True, "tiene": False}
        if "from public.tenant_users" in sql:
            auth_id = args[1]
            existente = self.base.tenant_users.get(auth_id)
            return None if existente is None else {"id": existente}
        raise AssertionError(f"fetchrow inesperado: {sql}")

    async def fetchval(self, sql, *args):
        self.base.sql.append(sql)
        if "write_audit_log" in sql:
            self.base.auditoria.append(args)
            return uuid.uuid4()
        raise AssertionError(f"fetchval inesperado: {sql}")


class _Pool:
    def __init__(self, base):
        self.base = base

    @asynccontextmanager
    async def acquire(self):
        yield _Conn(self.base)


class _ClienteFalso:
    def __init__(self, *, fallos=0, reintentable=True):
        self.fallos = fallos
        self.reintentable = reintentable
        self.llamadas: list[str] = []
        self.nombres: list[str | None] = []
        self.ids: dict[str, uuid.UUID] = {}

    async def invitar_por_email(self, email: str, nombre: str | None = None) -> uuid.UUID:
        self.llamadas.append(email)
        self.nombres.append(nombre)
        if self.fallos:
            self.fallos -= 1
            raise ErrorInvitacion("la API falló", reintentable=self.reintentable)
        return self.ids.setdefault(email, uuid.uuid4())


def _sesion():
    return SesionDashboard(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), is_owner=True, aal="aal2", identidad=None)


async def _invitar(
    base, cliente, email="Nueva@Ejemplo.invalid", permisos=("view_bookings", "approve_bookings"), nombre="Ana Pérez"
):
    return await invitar_staff(
        email, list(permisos), nombre=nombre, sesion=_sesion(), pool=_Pool(base), cliente=cliente
    )


async def test_owner_invita_crea_el_tenant_user_con_sus_permisos_y_llama_a_la_api():
    base, cliente = _BaseFalsa(), _ClienteFalso()
    r = await _invitar(base, cliente)
    assert r.creado is True
    assert base.tenant_users == {r.auth_user_id: r.tenant_user_id}
    assert base.permisos == {(r.tenant_user_id, "view_bookings"), (r.tenant_user_id, "approve_bookings")}
    assert cliente.llamadas == ["nueva@ejemplo.invalid"]  # normalizado


async def test_si_la_api_falla_no_queda_ningun_tenant_user_y_el_error_es_reintentable():
    base, cliente = _BaseFalsa(), _ClienteFalso(fallos=1)
    with pytest.raises(ErrorInvitacion) as e:
        await _invitar(base, cliente)
    assert e.value.reintentable is True
    assert base.tenant_users == {}
    assert base.permisos == set()
    assert base.auditoria == []


async def test_reintentar_tras_el_fallo_no_duplica():
    base, cliente = _BaseFalsa(), _ClienteFalso(fallos=1)
    with pytest.raises(ErrorInvitacion):
        await _invitar(base, cliente)
    r = await _invitar(base, cliente)
    assert len(base.tenant_users) == 1
    assert r.creado is True


async def test_reintentar_una_invitacion_ya_completada_no_duplica_ni_cambia_permisos():
    base, cliente = _BaseFalsa(), _ClienteFalso()
    primera = await _invitar(base, cliente)
    segunda = await _invitar(base, cliente, permisos=("edit_prices",))
    assert segunda.creado is False
    assert segunda.tenant_user_id == primera.tenant_user_id
    assert len(base.tenant_users) == 1
    assert {k for (_, k) in base.permisos} == {"view_bookings", "approve_bookings"}


async def test_si_la_base_falla_despues_de_invitar_el_reintento_reusa_la_cuenta_sin_duplicar():
    base, cliente = _BaseFalsa(), _ClienteFalso()
    base.fallar_insert_una_vez = True
    with pytest.raises(RuntimeError):
        await _invitar(base, cliente)
    assert base.tenant_users == {}
    r = await _invitar(base, cliente)
    assert len(base.tenant_users) == 1
    assert r.creado is True
    assert len(cliente.ids) == 1  # misma cuenta de Auth en ambos intentos


async def test_quien_no_es_owner_en_vivo_no_invita_y_la_api_no_se_llama():
    base, cliente = _BaseFalsa(es_owner=False), _ClienteFalso()
    with pytest.raises(NoEsOwner):
        await _invitar(base, cliente)
    assert cliente.llamadas == []
    assert base.tenant_users == {}


async def test_permiso_inexistente_se_rechaza_antes_de_llamar_a_la_api():
    base, cliente = _BaseFalsa(), _ClienteFalso()
    with pytest.raises(PermisoDesconocido):
        await _invitar(base, cliente, permisos=("view_bookings", "borrar_todo"))
    assert cliente.llamadas == []


@pytest.mark.parametrize("email", ["", "sin-arroba", "a@b@c.invalid", "con espacio@x.invalid", "x@", "@x.invalid"])
async def test_email_invalido_se_rechaza_antes_de_llamar_a_la_api(email):
    base, cliente = _BaseFalsa(), _ClienteFalso()
    with pytest.raises(EmailInvalido):
        await _invitar(base, cliente, email=email)
    assert cliente.llamadas == []


async def test_error_permanente_de_la_api_no_es_reintentable_y_no_deja_filas():
    base, cliente = _BaseFalsa(), _ClienteFalso(fallos=1, reintentable=False)
    with pytest.raises(ErrorInvitacion) as e:
        await _invitar(base, cliente)
    assert e.value.reintentable is False
    assert base.tenant_users == {}


async def test_el_audit_no_lleva_el_email():
    base, cliente = _BaseFalsa(), _ClienteFalso()
    await _invitar(base, cliente)
    assert len(base.auditoria) == 1
    assert "ejemplo.invalid" not in repr(base.auditoria[0])
    assert base.auditoria[0][0] == "staff_invited"


async def test_sin_permisos_asignados_es_valido():
    base, cliente = _BaseFalsa(), _ClienteFalso()
    r = await _invitar(base, cliente, permisos=())
    assert r.creado is True
    assert base.permisos == set()


async def test_el_nombre_viaja_a_la_api_limpio():
    base, cliente = _BaseFalsa(), _ClienteFalso()
    await _invitar(base, cliente, nombre="  Ana   Pérez \n")
    assert cliente.nombres == ["Ana Pérez"]


NOMBRES_INVALIDOS = [None, "", "   ", 5, "x" * 121, "con\x00nulo", "a\u202eb"]


@pytest.mark.parametrize("nombre", NOMBRES_INVALIDOS, ids=repr)
async def test_nombre_invalido_se_rechaza_antes_de_llamar_a_la_api_y_sin_repetir_el_valor(nombre):
    base, cliente = _BaseFalsa(), _ClienteFalso()
    with pytest.raises(NombreInvalido) as e:
        await _invitar(base, cliente, nombre=nombre)
    assert cliente.llamadas == []
    assert base.tenant_users == {}
    if isinstance(nombre, str) and len(nombre.strip()) > 3:
        assert nombre.strip() not in str(e.value)


async def test_el_audit_no_lleva_el_nombre():
    base, cliente = _BaseFalsa(), _ClienteFalso()
    await _invitar(base, cliente, nombre="Zulema Rarísima")
    assert "Zulema" not in repr(base.auditoria)


async def test_staff_ya_existe_usa_un_mensaje_neutro_que_no_revela_otro_negocio():
    base, cliente = _BaseFalsa(), _ClienteFalso()
    cuenta_vinculada, cuenta_con_invitacion = uuid.uuid4(), uuid.uuid4()
    cliente.ids = {"vinculada@ejemplo.invalid": cuenta_vinculada, "pendiente@ejemplo.invalid": cuenta_con_invitacion}
    base.en_otro_tenant = {cuenta_vinculada, cuenta_con_invitacion}

    errores = []
    for email in ("vinculada@ejemplo.invalid", "pendiente@ejemplo.invalid"):
        with pytest.raises(StaffYaExiste) as e:
            await _invitar(base, cliente, email=email)
        errores.append(e.value)

    a, b = errores
    assert (a.status_code, a.codigo, a.mensaje, str(a)) == (b.status_code, b.codigo, b.mensaje, str(b))
    assert (a.status_code, a.codigo) == (409, "staff_ya_existe")
    for palabra in ("negocio", "tenant", "otro", "pertenece", "pendiente", "invitaci"):
        assert palabra not in a.mensaje.lower()
