"""Soporte común de los tests del dashboard-backend: tokens efímeros, pool falso y Supabase Auth simulado.

Nada de claves reales: el secreto del JWT y la anon key se generan en cada corrida.
"""

import json
import secrets
import time
import uuid
from contextlib import asynccontextmanager

import httpx
import jwt

from dashboard_backend.app import crear_app
from dashboard_backend.config import Config
from dashboard_backend.supabase_auth import ClienteSupabaseAuth
from platform_auth import VerificadorJWT

AUD = "authenticated"
SECRETO = secrets.token_urlsafe(48)
ANON_KEY = "anon-" + secrets.token_urlsafe(24)
ORIGEN = "https://panel.ejemplo.test"
SUPABASE_URL = "https://supabase.ejemplo.test"
BASE = "https://api.ejemplo.test"


def token(*, user_id=None, tenant_id=None, is_owner=False, is_platform_admin=False, aal="aal1", exp=600):
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


class ConnFalsa:
    """Conexión asyncpg mínima: registra lo ejecutado y devuelve el estado vivo configurado."""

    def __init__(self):
        self.ejecutado: list[tuple[str, tuple]] = []
        self.estado_tenant = {"status": "active", "is_owner": False, "is_active": True}
        self.estado_staff = {"is_owner": False, "is_active": True, "tiene": True}
        self.ping_falla = False

    async def execute(self, sql, *args):
        self.ejecutado.append((sql, args))

    async def fetchrow(self, sql, *args):
        self.ejecutado.append((sql, args))
        if "from public.tenants t" in sql:
            return self.estado_tenant
        if "tenant_user_permissions" in sql:
            return self.estado_staff
        return None

    async def fetchval(self, sql, *args):
        if self.ping_falla:
            raise ConnectionError("detalle interno de la base: no debe filtrarse")
        return 1

    def sentencias(self) -> list[str]:
        return [s for s, _ in self.ejecutado]


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

    def fallar_red(self, metodo: str, ruta: str):
        self.rutas[(metodo, ruta)] = "red"

    def _atender(self, request: httpx.Request) -> httpx.Response:
        self.llamadas.append(request)
        destino = self.rutas.get((request.method, request.url.path))
        if destino is None:
            return httpx.Response(404, json={"msg": "ruta no simulada"})
        if destino == "red":
            raise httpx.ConnectError("sin red", request=request)
        estado, cuerpo = destino
        return httpx.Response(estado, json=cuerpo) if cuerpo is not None else httpx.Response(estado)

    def cuerpo(self, indice=-1):
        return json.loads(self.llamadas[indice].content or b"{}")

    def cliente(self) -> ClienteSupabaseAuth:
        return ClienteSupabaseAuth(SUPABASE_URL, ANON_KEY, http=self.cliente_http)


def config_prueba(**cambios) -> Config:
    base = {
        "database_url": "postgresql://no-se-usa@localhost/x",
        "supabase_url": SUPABASE_URL,
        "supabase_anon_key": ANON_KEY,
        "dashboard_origin": ORIGEN,
    }
    base.update(cambios)
    return Config(**base)


def app_de_prueba(*, supabase: SupabaseSimulado | None = None, pool=None, config: Config | None = None):
    supabase = supabase or SupabaseSimulado()
    pool = pool or PoolFalso()
    app = crear_app(
        config or config_prueba(),
        verificador=VerificadorJWT(algoritmos=["HS256"], audiencia=AUD, secreto=SECRETO),
        pool=pool,
        cliente_auth=supabase.cliente(),
        configurar_observabilidad=False,
    )
    return app, supabase, pool


def http(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)
