"""Doblés de test del bot: pool/conexión en memoria que registran el orden de las operaciones."""

import json
import secrets
import uuid

import httpx
import pytest


def etiqueta(sql: str) -> str:
    """Nombre corto de la operación, para afirmar el orden exacto de §2.6."""
    s = " ".join(sql.split()).lower()
    reglas = [
        ("resolve_inbound_tenant", "resolver_tenant"),
        ("set_config('app.current_tenant_id'", "contexto_tenant"),
        ("get_integration_secret", "leer_secret"),
        ("insert into public.inbound_messages", "dedup"),
        ("delete from public.bot_sessions", "sesion_descartar_o_borrar"),
        ("select state from public.bot_sessions", "sesion_leer"),
        ("insert into public.bot_sessions", "sesion_guardar"),
        ("current_user", "rol"),
    ]
    for trozo, nombre in reglas:
        if trozo in s:
            return nombre
    return s


class ConnFalsa:
    def __init__(self, pool: "PoolFalso") -> None:
        self.pool = pool
        self.pendientes: set[str] = set()

    async def execute(self, sql: str, *args):
        s = sql.strip().upper()
        if s == "BEGIN":
            self.pool.eventos.append("begin")
            return "BEGIN"
        if s in ("COMMIT", "ROLLBACK"):
            self.pool.eventos.append(s.lower())
            if s == "COMMIT":
                self.pool.marcas |= self.pendientes
            self.pendientes.clear()
            return s
        nombre = etiqueta(sql)
        self.pool.eventos.append(nombre)
        if nombre == "contexto_tenant":
            self.pool.contexto = args[0]
        if nombre == "dedup":
            update_id = args[1]
            if update_id in self.pool.marcas or update_id in self.pendientes:
                return "INSERT 0 0"
            self.pendientes.add(update_id)
            return "INSERT 0 1"
        if nombre == "sesion_guardar":
            self.pool.sesiones[args[2]] = json.loads(args[3])
        if nombre == "sesion_descartar_o_borrar" and "updated_at" not in sql:
            self.pool.sesiones.pop(args[2], None)
        return "OK"

    async def fetchval(self, sql: str, *args):
        self.pool.eventos.append(etiqueta(sql))
        if "get_integration_secret" in sql:
            return self.pool.secret
        return None

    async def fetchrow(self, sql: str, *args):
        nombre = etiqueta(sql)
        self.pool.eventos.append(nombre)
        if nombre == "sesion_leer":
            estado = self.pool.sesiones.get(args[2])
            return None if estado is None else {"state": json.dumps(estado)}
        return None


class _Adquisicion:
    def __init__(self, pool: "PoolFalso") -> None:
        self.pool = pool

    async def __aenter__(self):
        return ConnFalsa(self.pool)

    async def __aexit__(self, *exc):
        return False


class PoolFalso:
    def __init__(self, tenant_id, secret_json: str | None) -> None:
        self.tenant_id = tenant_id  # None = el webhook_key no resuelve a ningún tenant activo
        self.integration_id = uuid.uuid4()
        self.secret = secret_json
        self.eventos: list[str] = []
        self.marcas: set[str] = set()
        self.sesiones: dict[str, dict] = {}
        self.contexto: str | None = None
        self.rol = "bot_service"
        self.falla_health = False

    def acquire(self):
        return _Adquisicion(self)

    async def fetchrow(self, sql: str, *args):
        nombre = etiqueta(sql)
        self.eventos.append(nombre)
        if nombre == "resolver_tenant":
            if self.tenant_id is None:
                return None
            return {"tenant_id": self.tenant_id, "integration_id": self.integration_id}
        if nombre == "rol":
            if self.falla_health:
                raise ConnectionError("sin base")
            return {"actual": self.rol, "sesion": self.rol}
        return None


@pytest.fixture
def datos_bot():
    """Token y webhook_secret generados en cada corrida: sin credenciales literales en el repo."""
    token = f"{secrets.randbelow(10**9)}:{secrets.token_urlsafe(24)}"
    webhook_secret = secrets.token_urlsafe(24)
    return token, webhook_secret


@pytest.fixture
def pool(datos_bot):
    token, webhook_secret = datos_bot
    return PoolFalso(uuid.uuid4(), json.dumps({"bot_token": token, "webhook_secret": webhook_secret}))


class TelegramFalso:
    """Transporte httpx simulado de la Bot API; registra cada llamada en el log de eventos del pool."""

    def __init__(self, pool: PoolFalso) -> None:
        self.llamadas: list[dict] = []
        self._pool = pool

    def handler(self, request: httpx.Request) -> httpx.Response:
        self._pool.eventos.append("http")
        self.llamadas.append({"url": str(request.url), "cuerpo": json.loads(request.content)})
        return httpx.Response(200, json={"ok": True})

    def cliente(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def update(update_id: int = 1, texto: str = "hola", usuario: int = 111) -> dict:
    return {
        "update_id": update_id,
        "message": {"message_id": 1, "from": {"id": usuario}, "chat": {"id": usuario}, "text": texto},
    }
