"""Soporte de los tests de turnos y packs: conexión falsa que habla el SQL de `turnos`, calendario y bot simulados.

Datos ficticios: nombres inventados y teléfonos del rango de ejemplo. El bot de Telegram va con transporte HTTP
simulado (`httpx.MockTransport`): ningún test sale a la red.
"""

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone  # noqa: UP017 (el entorno local puede ser 3.10)

import httpx
from platform_auth import VerificadorJWT
from platform_calendar import AccionSync, ResultadoSync
from platform_notifications import TelegramAdapter
from turnos_dominio import alta as dominio
from turnos_queries import consultas as q

from dashboard_backend import turnos as t
from dashboard_backend.app import crear_app
from dashboard_backend.avisos_turnos import OpcionesAvisos
from dashboard_backend.tests.soporte import AUD, SECRETO, ConnFalsa, PoolFalso, SupabaseSimulado, config_prueba

INICIO = datetime(2030, 3, 4, 13, 0, tzinfo=timezone.utc)  # noqa: UP017 (10:00 en Buenos Aires)
DURACION = timedelta(minutes=30)
CHAT_ID = "555000111"
TOKEN_BOT = "token-falso-de-prueba"


def fila_turno(tenant_id, **cambios):
    """Una fila como la devuelve `SQL_TURNO`: pending futuro, cliente identificable con Telegram."""
    base = {
        "id": uuid.uuid4(),
        "tenant_id": tenant_id,
        "end_customer_id": uuid.uuid4(),
        "service_id": uuid.uuid4(),
        "professional_id": uuid.uuid4(),
        "start_at": INICIO,
        "end_at": INICIO + DURACION,
        "status": "pending",
        "deposit_status": "not_required",
        "booking_pack_id": None,
        "pending_expires_at": INICIO - timedelta(hours=2),
        "vencido": False,
        "google_calendar_event_id": None,
        "customer_name": "Cliente de prueba",
        "customer_phone": "+5491155500101",
        "customer_channel": "telegram",
        "customer_channel_user_id": CHAT_ID,
        "customer_deleted_at": None,
        "service_name": "Corte",
        "professional_name": "Profesional de prueba",
        "professional_auth_user_id": None,
        # Columnas de las consultas de listado y detalle (`SQL_TURNO` no las trae; el modelo de respuesta las exige).
        "created_at": INICIO - timedelta(days=1),
        "service_duration_minutes": 30,
        "service_price": 15000,
    }
    base.update(cambios)
    return base


class ConnTurnos(ConnFalsa):
    """Responde el SQL de `turnos` con estado en memoria y registra lo ejecutado (en orden, con COMMIT/ROLLBACK)."""

    def __init__(self):
        super().__init__()
        self.turnos: dict[uuid.UUID, dict] = {}
        self.auditoria: list[tuple] = []
        self.fallas: dict[str, Exception] = {}
        # Fallos que se agotan: cada sentencia lanza el siguiente de la lista (p. ej. deadlock) y luego funciona.
        self.fallas_transitorias: dict[str, list[Exception]] = {}
        self.datos_alta: dict | None = None
        self.pack_consumible = True
        self.eliminados: dict[uuid.UUID, object] = {}
        self.packs_insertados: list[tuple] = []
        self.turnos_insertados: list[tuple] = []
        self.packs: list[dict] = []
        self.cliente_pack: dict | None = None
        self.listado: list[dict] = []
        self.detalle: dict | None = None
        self.vencidos_liberables: list[dict] = []  # lo que devuelve `release_expired_pending_overlaps`

    def sentencias_que_contienen(self, texto: str) -> list[str]:
        return [s for s in self.sentencias() if texto in s]

    def ejecuciones(self, sql: str) -> list[tuple]:
        return [a for s, a in self.ejecutado if s == sql]

    def _falla(self, sql):
        if self.fallas_transitorias.get(sql):
            raise self.fallas_transitorias[sql].pop(0)
        if sql in self.fallas:
            raise self.fallas[sql]

    async def fetchrow(self, sql, *args):
        if sql == dominio.SQL_INSERTAR_TURNO:
            self.ejecutado.append((sql, args))
            self._falla(sql)
            # Se guarda con la forma de siempre: tenant, cliente, servicio, profesional, inicio, fin, pack.
            self.turnos_insertados.append((*args[:6], args[8]))
            nuevo = uuid.uuid4()
            self.turnos[nuevo] = fila_turno(
                args[0],
                id=nuevo,
                end_customer_id=args[1],
                service_id=args[2],
                professional_id=args[3],
                start_at=args[4],
                end_at=args[5],
                status="confirmed",
                pending_expires_at=None,
                booking_pack_id=args[8],
            )
            return {"id": nuevo, "status": "confirmed", "pending_expires_at": None}
        if sql == dominio.SQL_CONSUMIR_PACK:
            self.ejecutado.append((sql, args))
            return {"id": uuid.uuid4()} if self.pack_consumible else None
        if sql == dominio.SQL_MOVER_TURNO:
            self.ejecutado.append((sql, args))
            self._falla(sql)
            fila = self.turnos.get(args[1])
            if fila is None:
                return None
            fila["start_at"], fila["end_at"] = args[2], args[3]
            return {"id": args[1]}
        if sql == t.SQL_TURNO:
            self.ejecutado.append((sql, args))
            fila = self.turnos.get(args[1])
            return fila if fila is not None and fila["tenant_id"] == args[0] else None
        if sql == t.SQL_DATOS_ALTA:
            self.ejecutado.append((sql, args))
            return self.datos_alta
        if sql == t.SQL_INSERTAR_PACK:
            self.ejecutado.append((sql, args))
            self.packs_insertados.append(args)
            return {
                "id": uuid.uuid4(),
                "tenant_id": args[0],
                "end_customer_id": args[1],
                "service_id": args[2],
                "total_sessions": args[3],
                "used_sessions": 0,
                "origin": "manual_grant",
                "payment_status": "not_required",
                "expires_at": args[4],
                "created_at": INICIO,
            }
        if sql == q.SQL_DETALLE_BOOKING:
            self.ejecutado.append((sql, args))
            return self.detalle
        if sql == t.SQL_CLIENTE_PACK:
            self.ejecutado.append((sql, args))
            return self.cliente_pack
        return await super().fetchrow(sql, *args)

    async def fetchval(self, sql, *args):
        if "record_notification_attempt" in sql:
            self.ejecutado.append((sql, args))
            return uuid.uuid4()
        if "enqueue_notification" in sql:
            self.ejecutado.append((sql, args))
            if "enqueue_notification" in self.fallas:
                raise self.fallas["enqueue_notification"]
            return uuid.uuid4()
        if "complete_notification_outbox" in sql:
            self.ejecutado.append((sql, args))
            return True
        if "write_audit_log" in sql:
            self.ejecutado.append((sql, args))
            self.auditoria.append(args)
            return uuid.uuid4()
        return await super().fetchval(sql, *args)

    async def fetch(self, sql, *args):
        self.ejecutado.append((sql, args))
        if sql == dominio.SQL_LIBERAR_VENCIDOS:
            return self.vencidos_liberables
        if sql in (q.SQL_LISTAR_BOOKINGS, q.SQL_LISTAR_PACKS):
            return self.listado
        if sql == t.SQL_CLIENTES_ELIMINADOS:
            return [{"id": k, "deleted_at": v} for k, v in self.eliminados.items() if k in args[1]]
        return []

    async def execute(self, sql, *args):
        await super().execute(sql, *args)
        self._falla(sql)
        fila = self.turnos.get(args[1]) if sql in (t.SQL_CAMBIAR_ESTADO, t.SQL_GUARDAR_EVENTO) else None
        if sql == t.SQL_CAMBIAR_ESTADO:
            fila["status"] = args[2]
            fila["pending_expires_at"] = None
        elif sql == t.SQL_GUARDAR_EVENTO:
            fila["google_calendar_event_id"] = args[2]


class PoolTurnos(PoolFalso):
    def __init__(self, conn: ConnTurnos | None = None):
        self.conn = conn or ConnTurnos()


@dataclass(frozen=True)
class LlamadaCalendario:
    status: str
    professional_id: uuid.UUID
    google_calendar_event_id: str | None
    inicio: datetime | None = None
    fin: datetime | None = None
    titulo: str | None = None
    descripcion: str | None = None


class CalendarioSimulado:
    """Solo lo que usa el endpoint de `ServicioCalendario`: `sincronizar_evento` y `retirar_evento`.

    Cada llamada queda en `llamadas` como `LlamadaCalendario` (estado, id del evento, horario, título y descripción).
    """

    def __init__(self, conn: ConnTurnos | None = None, error: Exception | None = None):
        self.conn = conn
        self.error = error
        self.llamadas: list = []
        self.sentencias_al_sincronizar: list[str] | None = None

    def _anotar(self, llamada: "LlamadaCalendario") -> None:
        self.llamadas.append(llamada)
        self.sentencias_al_sincronizar = self.conn.sentencias()[:] if self.conn is not None else None
        if self.error is not None:
            raise self.error

    async def sincronizar_evento(self, profesional_id, evento):
        self._anotar(
            LlamadaCalendario(
                "confirmed",
                profesional_id,
                evento.id_externo,
                evento.inicio,
                evento.fin,
                evento.titulo,
                evento.descripcion,
            )
        )
        if evento.id_externo:
            return ResultadoSync(AccionSync.ACTUALIZADO, evento.id_externo)
        return ResultadoSync(AccionSync.CREADO, "evt-nuevo")

    async def retirar_evento(self, profesional_id, id_externo):
        self._anotar(LlamadaCalendario("cancelled", profesional_id, id_externo))
        return ResultadoSync(AccionSync.BORRADO, None) if id_externo else ResultadoSync(AccionSync.IGNORADO, None)


def fabrica_de(servicio=None, error_al_armar: Exception | None = None):
    @asynccontextmanager
    async def _fabrica(_conn):
        if error_al_armar is not None:
            raise error_al_armar
        yield servicio

    return _fabrica


class BotSimulado:
    """Telegram simulado a nivel HTTP: guarda cada `sendMessage` (destinatario y texto) o responde con error."""

    def __init__(self, estado: int = 200):
        self.estado = estado
        self.mensajes: list[dict] = []
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(self._atender))

    def _atender(self, request: httpx.Request) -> httpx.Response:
        import json

        self.mensajes.append(json.loads(request.content))
        if self.estado == 200:
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(self.estado, json={"ok": False, "error_code": self.estado, "description": "fallo"})

    def opciones(self, *, sin_integracion: Exception | None = None) -> OpcionesAvisos:
        async def _adaptador(_conn, cliente):
            if sin_integracion is not None:
                raise sin_integracion
            return TelegramAdapter(TOKEN_BOT, cliente)

        async def _no_esperar(_segundos):
            return None

        return OpcionesAvisos(
            cliente_http=self.http, fabrica_adaptador=_adaptador, backoff=(0.0, 0.0, 0.0), esperar=_no_esperar
        )


def app_con_pool(pool, *, calendario=None, fabrica=None, bot: BotSimulado | None = None):
    """App sobre un pool dado (en integración, el real). Devuelve `(app, calendario, bot)`."""
    calendario = calendario or CalendarioSimulado()
    bot = bot or BotSimulado()
    app = crear_app(
        config_prueba(),
        verificador=VerificadorJWT(algoritmos=["HS256"], audiencia=AUD, secreto=SECRETO),
        pool=pool,
        cliente_auth=SupabaseSimulado().cliente(),
        configurar_observabilidad=False,
        fabrica_calendario=fabrica or fabrica_de(calendario),
        opciones_avisos=bot.opciones(),
    )
    return app, calendario, bot


def app_turnos(*, conn: ConnTurnos | None = None, calendario=None, fabrica=None, bot: BotSimulado | None = None):
    """App con la base falsa. Devuelve `(app, conn, calendario, bot)`."""
    pool = PoolTurnos(conn)
    conn = pool.conn
    if calendario is None and fabrica is None:
        calendario = CalendarioSimulado(conn)
    bot = bot or BotSimulado()
    app = crear_app(
        config_prueba(),
        verificador=VerificadorJWT(algoritmos=["HS256"], audiencia=AUD, secreto=SECRETO),
        pool=pool,
        cliente_auth=SupabaseSimulado().cliente(),
        configurar_observabilidad=False,
        fabrica_calendario=fabrica or fabrica_de(calendario),
        opciones_avisos=bot.opciones(),
    )
    return app, conn, calendario, bot


def vencido_liberable(tenant_id, **cambios):
    """Una fila como la devuelve `release_expired_pending_overlaps`: pending vencido de un cliente con Telegram."""
    base = {
        "tenant_id": tenant_id,
        "booking_id": uuid.uuid4(),
        "channel": "telegram",
        "channel_user_id": "777000222",
        "notify": True,
        "end_customer_id": uuid.uuid4(),
        "starts_at": INICIO,
        "service_name": "Corte",
    }
    base.update(cambios)
    return base
