"""Soporte de los tests de la configuración de avisos: conexión falsa que imita lo que la base real exige.

El doble no acepta nada que la base rechazaría: WhatsApp activado (check), una persona que no es del tenant (clave
foránea compuesta), una fila duplicada (clave primaria) y dejar al negocio sin ningún destinatario de email (trigger de
restricción, inmediato salvo que se lo difiera con `set constraints`). `ROLLBACK` restaura el estado: lo que una
petición rechazada intentó escribir no queda.
"""

import copy
import uuid
from datetime import datetime, timezone  # noqa: UP017 (el entorno local puede ser 3.10)

from asyncpg.exceptions import CheckViolationError, ForeignKeyViolationError, UniqueViolationError
from platform_auth import VerificadorJWT

from dashboard_backend import avisos_negocio as a
from dashboard_backend.app import crear_app
from dashboard_backend.staff import SQL_IDENTIDAD
from dashboard_backend.tests.soporte import AUD, SECRETO, ConnFalsa, PoolFalso, SupabaseSimulado, config_prueba

MENSAJE_SIN_DESTINATARIO = "el negocio no puede quedar sin ningún destinatario de avisos por email"


class _CheckWhatsapp(CheckViolationError):
    constraint_name = a.RESTRICCION_WHATSAPP


class ConnAvisos(ConnFalsa):
    def __init__(self):
        super().__init__()
        self.staff: dict[uuid.UUID, dict] = {}
        self.config: dict[uuid.UUID, dict] = {}
        self.identidades: dict[uuid.UUID, dict] = {}
        self.diferido = False
        # Quien llama: sus permisos reales (la comprobación de permiso mira la clave pedida, no un "sí" genérico).
        self.llamador = {"is_owner": False, "permisos": {"manage_staff"}}
        self.falla_al_escribir: Exception | None = None  # p. ej. un deadlock que aborta la transacción
        self._respaldo: tuple | None = None

    def agregar(self, tenant, *, is_owner=False, is_active=True, permisos=(), es_yo=False) -> uuid.UUID:
        i = uuid.uuid4()
        self.staff[i] = {
            "id": i,
            "tenant": tenant,
            "is_owner": is_owner,
            "is_active": is_active,
            "es_yo": es_yo,
            "permisos": set(permisos),
            "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),  # noqa: UP017
        }
        return i

    def poner_config(self, staff_id, recibe=True, email=True, whatsapp=False):
        self.config[staff_id] = {"recibe_avisos": recibe, "canal_email": email, "canal_whatsapp": whatsapp}

    def escrituras_de_config(self) -> list[str]:
        return [s for s in self.sentencias() if s in (a.SQL_INSERTAR, a.SQL_ACTUALIZAR, a.SQL_BORRAR)]

    # --- reglas de la base -------------------------------------------------------------------------------------

    def _recibe_por_email(self, i) -> bool:
        p = self.staff[i]
        c = self.config.get(i)
        if c is None:
            return p["is_owner"] or "approve_bookings" in p["permisos"]
        return c["recibe_avisos"] and c["canal_email"]

    def _destinatarios(self, tenant) -> int:
        return sum(
            1 for i, p in self.staff.items() if p["tenant"] == tenant and p["is_active"] and self._recibe_por_email(i)
        )

    def _exigir_destinatario(self, tenant) -> None:
        hay_owner = any(p["tenant"] == tenant and p["is_owner"] and p["is_active"] for p in self.staff.values())
        if hay_owner and self._destinatarios(tenant) < 1:
            raise CheckViolationError(MENSAJE_SIN_DESTINATARIO)

    def _tras_escribir(self, tenant) -> None:
        if not self.diferido:
            self._exigir_destinatario(tenant)

    # --- conexión ----------------------------------------------------------------------------------------------

    async def execute(self, sql, *args):
        await super().execute(sql, *args)
        if sql == "BEGIN":
            self._respaldo = (copy.deepcopy(self.staff), copy.deepcopy(self.config), list(self.auditoria))
            self.diferido = False
        elif sql == "ROLLBACK" and self._respaldo is not None:
            self.staff, self.config, self.auditoria = self._respaldo
            self.diferido = False
        elif sql == a.SQL_DIFERIR_GARANTIA:
            self.diferido = True
        elif sql == a.SQL_EXIGIR_GARANTIA:
            self.diferido = False
            for tenant in {p["tenant"] for p in self.staff.values()}:
                self._exigir_destinatario(tenant)
        elif sql in (a.SQL_INSERTAR, a.SQL_ACTUALIZAR):
            if self.falla_al_escribir is not None:
                raise self.falla_al_escribir
            tenant, ids, recibe, email, whatsapp = args
            for i, r, e, w in zip(ids, recibe, email, whatsapp, strict=True):
                if w:
                    raise _CheckWhatsapp("new row violates check constraint")
                if sql == a.SQL_INSERTAR:
                    if i not in self.staff or self.staff[i]["tenant"] != tenant:
                        raise ForeignKeyViolationError("violates foreign key constraint")
                    if i in self.config:
                        raise UniqueViolationError("duplicate key value")
                elif i not in self.config:
                    continue  # el update no toca lo que no existe
                self.config[i] = {"recibe_avisos": r, "canal_email": e, "canal_whatsapp": w}
            self._tras_escribir(tenant)
        elif sql == a.SQL_BORRAR:
            tenant, ids = args
            for i in ids:
                self.config.pop(i, None)
            self._tras_escribir(tenant)

    async def fetchrow(self, sql, *args):
        if len(args) == 3 and "as tiene" in sql:  # la comprobación de permiso de `platform_auth`
            self.ejecutado.append((sql, args))
            clave = getattr(args[2], "value", args[2])
            return {
                "is_owner": self.llamador["is_owner"],
                "is_active": True,
                "tiene": clave in self.llamador["permisos"],
            }
        if "from public.tenants t" in sql:
            self.ejecutado.append((sql, args))
            return {"status": "active", "is_owner": self.llamador["is_owner"], "is_active": True}
        return await super().fetchrow(sql, *args)

    async def fetch(self, sql, *args):
        if sql == a.SQL_LISTAR:
            self.ejecutado.append((sql, args))
            tenant, usuario = args
            filas = []
            for i, p in self.staff.items():
                if p["tenant"] != tenant or not p["is_active"]:
                    continue
                c = self.config.get(i)
                por_defecto = p["is_owner"] or "approve_bookings" in p["permisos"]
                filas.append(
                    {
                        "id": i,
                        "is_owner": p["is_owner"],
                        "es_yo": p["es_yo"],
                        "es_default": c is None,
                        "recibe_avisos": por_defecto if c is None else c["recibe_avisos"],
                        "canal_email": por_defecto if c is None else c["canal_email"],
                        "canal_whatsapp": False if c is None else c["canal_whatsapp"],
                    }
                )
            return filas
        if sql == SQL_IDENTIDAD:
            self.ejecutado.append((sql, args))
            return [
                {"tenant_user_id": i, "email": d.get("email"), "nombre": d.get("nombre")}
                for i, d in self.identidades.items()
            ]
        if sql == a.SQL_BLOQUEAR_STAFF:
            self.ejecutado.append((sql, args))
            tenant, ids = args
            return [
                {"id": i, "is_active": self.staff[i]["is_active"]}
                for i in sorted(ids, key=str)
                if i in self.staff and self.staff[i]["tenant"] == tenant
            ]
        if sql == a.SQL_ACTUALES:
            self.ejecutado.append((sql, args))
            tenant, ids = args
            return [{"tenant_user_id": i, **self.config[i]} for i in ids if i in self.config]
        return await super().fetch(sql, *args)


def app_avisos():
    """App con el pool falso. Devuelve `(app, conn)`."""
    conn = ConnAvisos()
    pool = PoolFalso()
    pool.conn = conn
    app = crear_app(
        config_prueba(),
        verificador=VerificadorJWT(algoritmos=["HS256"], audiencia=AUD, secreto=SECRETO),
        pool=pool,
        cliente_auth=SupabaseSimulado().cliente(),
        configurar_observabilidad=False,
    )
    return app, conn
