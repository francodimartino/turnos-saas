"""Soporte de los tests de la conexión de Google Calendar: conexión falsa que habla el SQL de `conexion_calendar` y un
Google simulado con transporte HTTP mockeado (`httpx.MockTransport`): ningún test sale a la red.

Nada de credenciales reales: el proyecto Google de prueba se genera al azar en cada corrida.
"""

import json
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone  # noqa: UP017 (el entorno local puede ser 3.10)

import httpx
from platform_auth import VerificadorJWT
from platform_calendar import ConfigGoogle, crear_calendario
from platform_secrets import crear_almacen

from dashboard_backend import conexion_calendar as rc
from dashboard_backend.app import crear_app
from dashboard_backend.tests.soporte import AUD, SECRETO, ConnFalsa, PoolFalso, SupabaseSimulado, config_prueba

REDIRECT_URI = "https://api.ejemplo.test/calendar/callback"


def config_google() -> ConfigGoogle:
    return ConfigGoogle(
        client_id="cliente-" + secrets.token_hex(6),
        client_secret=secrets.token_urlsafe(24),
        redirect_uri=REDIRECT_URI,
        state_secret=secrets.token_bytes(32),
    )


class GoogleSimulado:
    """Endpoint de tokens de Google simulado: registra los pedidos y responde lo configurado."""

    def __init__(self, *, estado: int = 200, cuerpo: dict | None = None, red: bool = False):
        self.config = config_google()
        self.refresh_token = "refresco-" + secrets.token_urlsafe(16)
        self.estado = estado
        self.cuerpo = cuerpo
        self.red = red
        self.pedidos: list[httpx.Request] = []

    def _atender(self, request: httpx.Request) -> httpx.Response:
        self.pedidos.append(request)
        if self.red:
            raise httpx.ConnectError("sin red", request=request)
        cuerpo = self.cuerpo
        if cuerpo is None:
            cuerpo = {"access_token": "acceso-" + secrets.token_urlsafe(12), "refresh_token": self.refresh_token}
        return httpx.Response(self.estado, json=cuerpo)

    def servicio(self, conn, reloj=time.time, cache_tokens=None):
        http = httpx.AsyncClient(transport=httpx.MockTransport(self._atender))
        return crear_calendario(self.config, crear_almacen(conn), http, reloj, cache_tokens)

    def fabrica(self, cache_tokens=None):
        """`cache_tokens`: dict compartido entre los servicios que arma (como la caché de proceso del proveedor)."""

        @asynccontextmanager
        async def _fabrica(conn):
            yield self.servicio(conn, cache_tokens=cache_tokens)

        return _fabrica

    def state_de(self, tenant_id, professional_id, usuario_id, *, antes_seg: float = 0.0) -> str:
        """Un `state` firmado con la clave de este Google de prueba, atado a `usuario_id` (la sesión que inicia la
        conexión). `antes_seg` > 0 lo emite en el pasado."""
        servicio = self.servicio(None, reloj=lambda: time.time() - antes_seg)
        url = servicio.url_autorizacion(tenant_id, professional_id, usuario_id)
        return httpx.URL(url).params["state"]


class ConnCalendar(ConnFalsa):
    """Responde el SQL de `conexion_calendar` con estado en memoria; registra la auditoría de las funciones SQL."""

    def __init__(self):
        super().__init__()
        # professional_id -> {"tenant_id", "is_active", "vinculado_a": auth_user_id | None}
        self.profesionales: dict[uuid.UUID, dict] = {}
        self.integraciones: dict[uuid.UUID, dict] = {}  # professional_id -> {"id", "config", "secret"}
        self.auditoria: list[tuple[str, uuid.UUID]] = []
        self.nonces_consumidos: set[str] = set()  # lo que hace `consume_oauth_state_nonce` en la base

    def agregar_profesional(self, tenant_id, *, activo=True, vinculado_a=None) -> uuid.UUID:
        i = uuid.uuid4()
        self.profesionales[i] = {"tenant_id": tenant_id, "is_active": activo, "vinculado_a": vinculado_a}
        return i

    def conectar(self, professional_id) -> None:
        self.integraciones[professional_id] = {"id": uuid.uuid4(), "config": {}, "secret": None}

    def sentencias_de_escritura_directa(self) -> list[str]:
        """INSERT/UPDATE/DELETE sobre `tenant_integrations` (no debería haber: se escribe solo por funciones SQL)."""
        return [
            s
            for s in self.sentencias()
            if "tenant_integrations" in s.lower() and s.lstrip().lower().startswith(("insert", "update", "delete"))
        ]

    async def fetchrow(self, sql, *args):
        if sql == rc.SQL_PROFESIONAL:
            self.ejecutado.append((sql, args))
            tenant_id, professional_id, auth_user_id = args
            p = self.profesionales.get(professional_id)
            if p is None or p["tenant_id"] != tenant_id:
                return None
            return {
                "id": professional_id,
                "is_active": p["is_active"],
                "es_el_propio": p["vinculado_a"] is not None and p["vinculado_a"] == auth_user_id,
            }
        if sql == rc.SQL_INTEGRACION:
            self.ejecutado.append((sql, args))
            integracion = self.integraciones.get(args[1])
            if integracion is None:
                return None
            return {"created_at": datetime(2030, 1, 1, tzinfo=timezone.utc)}  # noqa: UP017
        return await super().fetchrow(sql, *args)

    async def fetchval(self, sql, *args):
        if sql == rc.SQL_CONSUMIR_NONCE:
            self.ejecutado.append((sql, args))
            nonce, expira = args
            if nonce in self.nonces_consumidos or expira.timestamp() < time.time():
                return False
            self.nonces_consumidos.add(nonce)
            return True
        if "store_professional_integration" in sql:
            self.ejecutado.append((sql, args))
            _tipo, professional_id, config, secret = args
            integracion = self.integraciones.setdefault(professional_id, {"id": uuid.uuid4()})
            integracion.update(config=json.loads(config), secret=secret)
            self.auditoria.append(("connect_google_calendar", professional_id))
            return integracion["id"]
        if "find_integration" in sql:
            self.ejecutado.append((sql, args))
            integracion = self.integraciones.get(args[1])
            return None if integracion is None else integracion["id"]
        if "delete_professional_integration" in sql:
            self.ejecutado.append((sql, args))
            existia = self.integraciones.pop(args[0], None) is not None
            if existia:
                self.auditoria.append(("disconnect_google_calendar", args[0]))
            return existia
        return await super().fetchval(sql, *args)


def app_calendar(*, google: GoogleSimulado | None = None, usar_fabrica_por_defecto=False, pool=None, fabrica=None):
    """App con el pool falso (o el real). Devuelve `(app, conn, google)`.

    Con `usar_fabrica_por_defecto` la app lee el proyecto Google del entorno (para probar el 503 sin configurar).
    """
    conn = None
    if pool is None:
        conn = ConnCalendar()
        pool = PoolFalso()
        pool.conn = conn
    google = google or GoogleSimulado()
    app = crear_app(
        config_prueba(),
        verificador=VerificadorJWT(algoritmos=["HS256"], audiencia=AUD, secreto=SECRETO),
        pool=pool,
        cliente_auth=SupabaseSimulado().cliente(),
        fabrica_calendario=None if usar_fabrica_por_defecto else (fabrica or google.fabrica()),
        configurar_observabilidad=False,
    )
    return app, conn, google
