"""Soporte de los tests de staff: conexión falsa que habla el SQL de `rutas_staff` y un cliente de Auth Admin simulado.

Datos ficticios. No hay emails reales: la invitación recibe un dominio reservado `.invalid`.
"""

import json
import uuid
from datetime import datetime, timezone  # noqa: UP017 (el entorno local puede ser 3.10)

import httpx
from asyncpg.exceptions import CheckViolationError
from platform_auth import SupabaseAuthAdmin, VerificadorJWT

from dashboard_backend import rutas_staff as s
from dashboard_backend.app import crear_app
from dashboard_backend.tests.soporte import AUD, SECRETO, ConnFalsa, PoolFalso, SupabaseSimulado, config_prueba

MENSAJE_ULTIMO_OWNER = "No se puede eliminar, desactivar ni quitar is_owner al último owner activo del tenant x"


class ConnStaff(ConnFalsa):
    def __init__(self):
        super().__init__()
        self.staff: dict[uuid.UUID, dict] = {}
        self.auditoria: list[tuple] = []
        self.identidades: dict[uuid.UUID, dict] = {}  # lo que devolvería get_tenant_staff_identity
        self.ultimo_owner = False  # simula que el trigger de la base rechaza el cambio

    def agregar(self, *, is_owner=False, is_active=True, permisos=(), es_yo=False) -> uuid.UUID:
        i = uuid.uuid4()
        self.staff[i] = {
            "id": i,
            "is_owner": is_owner,
            "is_active": is_active,
            "es_yo": es_yo,
            "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),  # noqa: UP017
            "permission_keys": sorted(permisos),
        }
        return i

    def sentencias_de_escritura(self) -> list[str]:
        return [x for x in self.sentencias() if x.lstrip().lower().startswith(("update", "delete", "insert"))]

    async def fetchrow(self, sql, *args):
        if sql == s.SQL_OBJETIVO:
            self.ejecutado.append((sql, args))
            return self.staff.get(args[1])
        if sql in (s.SQL_CAMBIAR_OWNER, s.SQL_CAMBIAR_ACTIVO):
            self.ejecutado.append((sql, args))
            if self.ultimo_owner:
                raise CheckViolationError(MENSAJE_ULTIMO_OWNER)
            fila = self.staff[args[1]]
            fila["is_owner" if sql == s.SQL_CAMBIAR_OWNER else "is_active"] = args[2]
            return fila
        if "insert into public.tenant_users" in sql:
            self.ejecutado.append((sql, args))
            return {"id": uuid.uuid4()}
        return await super().fetchrow(sql, *args)

    async def fetch(self, sql, *args):
        if sql == s.SQL_LISTAR:
            self.ejecutado.append((sql, args))
            return list(self.staff.values())
        if sql == s.SQL_IDENTIDAD:
            self.ejecutado.append((sql, args))
            return [
                {"tenant_user_id": i, "email": d.get("email"), "nombre": d.get("nombre")}
                for i, d in self.identidades.items()
            ]
        if sql == s.SQL_CATALOGO:
            self.ejecutado.append((sql, args))
            return [{"key": k} for k in self.catalogo_permisos]
        if sql == s.SQL_PERMISOS_DE:
            self.ejecutado.append((sql, args))
            return [{"permission_key": k} for k in self.staff[args[0]]["permission_keys"]]
        if sql == "select key from public.permissions":  # el que usa platform_auth.invitar_staff
            self.ejecutado.append((sql, args))
            return [{"key": k} for k in self.catalogo_permisos]
        return await super().fetch(sql, *args)

    async def fetchval(self, sql, *args):
        if "write_audit_log" in sql:
            self.ejecutado.append((sql, args))
            self.auditoria.append(args)
            return uuid.uuid4()
        return await super().fetchval(sql, *args)

    async def execute(self, sql, *args):
        await super().execute(sql, *args)
        if sql == s.SQL_QUITAR_PERMISOS:
            fila = self.staff[args[0]]
            fila["permission_keys"] = [k for k in fila["permission_keys"] if k in args[1]]
        elif sql == s.SQL_AGREGAR_PERMISOS and args[0] in self.staff:  # (el alta de invitar_staff usa el mismo SQL)
            fila = self.staff[args[0]]
            fila["permission_keys"] = sorted(set(fila["permission_keys"]) | set(args[1]))


class AuthAdminSimulado:
    """`ClienteAuthAdmin` de mentira: devuelve un id fijo o falla como la API real."""

    def __init__(self, auth_user_id: uuid.UUID | None = None, error=None):
        self.auth_user_id = auth_user_id or uuid.uuid4()
        self.error = error
        self.emails: list[str] = []
        self.nombres: list[str | None] = []

    async def invitar_por_email(self, email: str, nombre: str | None = None) -> uuid.UUID:
        self.emails.append(email)
        self.nombres.append(nombre)
        if self.error is not None:
            raise self.error
        return self.auth_user_id


def auth_admin_http(auth_user_id: uuid.UUID, estado: int = 200, peticiones: list | None = None) -> SupabaseAuthAdmin:
    """`SupabaseAuthAdmin` real con el HTTP mockeado (para los tests de integración).

    Si se pasa `peticiones`, guarda en ella el cuerpo JSON de cada invitación.
    """

    def _atender(request: httpx.Request) -> httpx.Response:
        if peticiones is not None:
            peticiones.append(json.loads(request.content))
        if estado != 200:
            return httpx.Response(estado, json={"msg": "no"})
        return httpx.Response(200, json={"id": str(auth_user_id)})

    return SupabaseAuthAdmin(
        "https://supabase.ejemplo.test",
        "service-role-de-prueba",
        http=httpx.AsyncClient(transport=httpx.MockTransport(_atender)),
    )


def app_staff(*, pool=None, cliente_admin=None):
    """App con el pool falso (o el real, si se pasa `pool`). Devuelve `(app, conn)`."""
    conn = None
    if pool is None:
        conn = ConnStaff()
        pool = PoolFalso()
        pool.conn = conn
    app = crear_app(
        config_prueba(),
        verificador=VerificadorJWT(algoritmos=["HS256"], audiencia=AUD, secreto=SECRETO),
        pool=pool,
        cliente_auth=SupabaseSimulado().cliente(),
        cliente_admin=cliente_admin,
        configurar_observabilidad=False,
    )
    return app, conn
