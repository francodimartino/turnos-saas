"""Soporte común de los tests del admin-backend: tokens efímeros, pool falso y Supabase Auth simulado.

Nada de claves reales: el secreto del JWT y las claves se generan en cada corrida.
"""

import json
import secrets
import time
import uuid
from contextlib import asynccontextmanager

import httpx
import jwt
from platform_auth import VerificadorJWT
from supabase_proxy import ClienteSupabaseAuth

from admin_backend.app import crear_app
from admin_backend.config import Config

AUD = "authenticated"
SECRETO = secrets.token_urlsafe(48)
ANON_KEY = "anon-" + secrets.token_urlsafe(24)
ORIGEN = "https://admin.ejemplo.test"
SUPABASE_URL = "https://supabase.ejemplo.test"
BASE = "https://api-admin.ejemplo.test"


def token(*, user_id=None, tenant_id=None, is_owner=False, is_platform_admin=True, aal="aal2", exp=600):
    return jwt.encode(
        {
            "sub": str(user_id or uuid.uuid4()),
            "aud": AUD,
            "exp": int(time.time()) + exp,
            "aal": aal,
            "tenant_id": None if tenant_id is None else str(tenant_id),
            "is_owner": is_owner,
            "is_platform_admin": is_platform_admin,
        },
        SECRETO,
        algorithm="HS256",
    )


def bearer(**kwargs) -> dict[str, str]:
    return {"Authorization": f"Bearer {token(**kwargs)}"}


class ConnFalsa:
    """Conexión asyncpg mínima: registra lo ejecutado; `es_admin` controla la confirmación en vivo."""

    def __init__(self):
        self.ejecutado: list[tuple[str, tuple]] = []
        self.es_admin = True
        self.ping_falla = False

    async def execute(self, sql, *args):
        self.ejecutado.append((sql, args))

    async def fetchrow(self, sql, *args):
        self.ejecutado.append((sql, args))
        if "platform_admins" in sql:
            return {"existe": True} if self.es_admin else None
        return None

    async def fetchval(self, sql, *args):
        if self.ping_falla:
            raise ConnectionError("detalle interno de la base: no debe filtrarse")
        return 1


class PoolFalso:
    def __init__(self):
        self.conn = ConnFalsa()

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


def sesion_json(access=None, refresh=None, expires_in=3600, factores=()):
    return {
        "access_token": access or "acceso-" + secrets.token_urlsafe(12),
        "refresh_token": refresh or "refresco-" + secrets.token_urlsafe(12),
        "token_type": "bearer",
        "expires_in": expires_in,
        "user": {"id": str(uuid.uuid4()), "factors": [dict(f) for f in factores]},
    }


class SupabaseSimulado:
    """Transporte HTTP simulado de Supabase Auth: registra cada request y responde lo configurado."""

    def __init__(self):
        self.llamadas: list[httpx.Request] = []
        self.rutas: dict[tuple[str, str], object] = {}
        self.cliente_http = httpx.AsyncClient(transport=httpx.MockTransport(self._atender))

    def responder(self, metodo: str, ruta: str, estado: int = 200, cuerpo=None):
        self.rutas[(metodo, ruta)] = (estado, cuerpo)

    def _atender(self, request: httpx.Request) -> httpx.Response:
        self.llamadas.append(request)
        destino = self.rutas.get((request.method, request.url.path))
        if destino is None:
            return httpx.Response(404, json={"msg": "ruta no simulada"})
        estado, cuerpo = destino
        return httpx.Response(estado, json=cuerpo) if cuerpo is not None else httpx.Response(estado)

    def cuerpo(self, indice=-1):
        return json.loads(self.llamadas[indice].content or b"{}")

    def cliente(self) -> ClienteSupabaseAuth:
        return ClienteSupabaseAuth(SUPABASE_URL, ANON_KEY, http=self.cliente_http)


class AuthAdminFalso:
    """Auth Admin API simulada (invitaciones): registra a quién se invitó."""

    def __init__(self, falla: bool = False):
        self.falla = falla
        self.invitados: list[str] = []
        self.nombres: list[str | None] = []

    async def invitar(self, email: str, nombre: str | None = None) -> uuid.UUID:
        if self.falla:
            from platform_admin import ErrorInvitacion

            raise ErrorInvitacion("simulado")
        self.invitados.append(email)
        self.nombres.append(nombre)
        return uuid.uuid4()


def config_prueba(**cambios) -> Config:
    base = {
        "database_url": "postgresql://no-se-usa@localhost/x",
        "readonly_database_url": "postgresql://no-se-usa-ro@localhost/x",
        "supabase_url": SUPABASE_URL,
        "supabase_anon_key": ANON_KEY,
        "admin_origin": ORIGEN,
    }
    base.update(cambios)
    return Config(**base)


def app_de_prueba(
    *,
    supabase: SupabaseSimulado | None = None,
    pool=None,
    pool_readonly=None,
    auth_admin=None,
    config: Config | None = None,
):
    supabase = supabase or SupabaseSimulado()
    pool = pool or PoolFalso()
    pool_readonly = pool_readonly or PoolFalso()
    app = crear_app(
        config or config_prueba(),
        verificador=VerificadorJWT(algoritmos=["HS256"], audiencia=AUD, secreto=SECRETO),
        pool=pool,
        pool_readonly=pool_readonly,
        cliente_auth=supabase.cliente(),
        cliente_auth_admin=auth_admin or AuthAdminFalso(),
        configurar_observabilidad=False,
    )
    return app, supabase, pool


def http(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)
