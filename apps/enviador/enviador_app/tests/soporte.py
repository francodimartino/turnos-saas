"""Dobles de prueba del enviador: una base falsa que responde lo mismo que las funciones reales y canales simulados.

Lo que depende de SQL (qué devuelve `resolve_outbox_delivery`, el reclamo con SKIP LOCKED, el aislamiento entre tenants)
se prueba contra Postgres en los tests de integración: acá las respuestas de la base se fijan a mano, con las columnas y
los tipos que devuelven las funciones.
"""

import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from platform_tenancy.testing import TransaccionPorExecute
from turnos_email import ErrorEmail

UTC = timezone.utc  # noqa: UP017
INICIO = datetime(2026, 10, 21, 15, 30, tzinfo=UTC)
CHAT = "5491155550077"
EMAIL_CLIENTE = "ana.perez@ejemplo.test"
EMAIL_NEGOCIO = "duenia@negocio.test"
TOKEN = f"123456:{uuid.uuid4().hex}"


def fila(tenant, template="turno_confirmado", intentos=1, *, negocio=False):
    """Una fila de `claim_notification_outbox` (las del negocio no tienen ficha ni canal de origen)."""
    return {
        "id": uuid.uuid4(),
        "tenant_id": tenant,
        "end_customer_id": None if negocio else uuid.uuid4(),
        "channel": None if negocio else "telegram",
        "template": template,
        "params": json.dumps({"booking_id": str(uuid.uuid4()), "inicio": INICIO.isoformat()}),
        "attempts": intentos,
    }


def destino(canal, identificador, *, clave=None, entregado=False, destino_fila="cliente", **cambios):
    """Una fila de `resolve_outbox_delivery`."""
    base = {
        "channel": canal,
        "channel_user_id": identificador,
        "tenant_active": True,
        "booking_status": "confirmed",
        "service_name": "Corte de pelo",
        "professional_name": "Ana",
        "starts_at": INICIO,
        "pending_expires_at": None,
        "destino": destino_fila,
        "clave": clave or f"cc:{canal}",
        "entregado": entregado,
        "anonimizada": False,
    }
    return {**base, **cambios}


def telegram(**cambios):
    return destino("telegram", CHAT, clave="cc:tg", **cambios)


def email(**cambios):
    return destino("email", EMAIL_CLIENTE, clave="cc:mail", **cambios)


def sin_canal(**cambios):
    """Lo que devuelve la función cuando no hay a quién enviar: una fila con canal e identificador nulos."""
    return destino(None, None, clave=None, **cambios)


class Mundo:
    """Estado de la base falsa: lo que cada función recibió, con el contexto de tenant de la transacción."""

    def __init__(self):
        self.destinos = {}  # id de la fila -> filas de resolve_outbox_delivery
        self.entregas = {}  # (id, clave) -> (estado, error): lo registrado, que resolve devuelve como `entregado`
        self.cierres = []  # (id, status, error, retry, tenant del contexto)
        self.registros = []  # (tenant del contexto, end_customer_id, channel, template, status)
        self.integraciones = {}  # tenant -> secret (json) o None
        self.cierres_perdidos = set()  # ids de filas que otro proceso ya cerró
        self.falla_resolver = False
        self.falla_registrar_entrega = None
        self.falla_registro_envio = None
        self.falla_cierre = None
        self.entregas_llamadas = []  # (tenant del contexto, id, clave, estado, error)
        self.rol = "enviador"  # lo que responde la base sobre el rol de la conexión
        self.falla_fetchrow = None
        self.lecturas_de_secret = []  # tenant del contexto de cada lectura del token del bot
        self.cola = []  # filas pendientes que `claim_notification_outbox` entrega, en orden
        self.reclamos = []  # argumentos de cada reclamo
        self.falla_reclamo = []  # excepciones que lanzan los próximos reclamos (una por reclamo)
        self.tx_abiertas = 0  # transacciones abiertas en este momento (BEGIN sin su COMMIT/ROLLBACK)
        self.http_con_tx = []  # cuántas transacciones había abiertas en cada llamada externa (tiene que ser 0)

    def poner(self, fila_, destinos):
        self.destinos[fila_["id"]] = [] if destinos is None else destinos if isinstance(destinos, list) else [destinos]

    def con_bot(self, tenant, token=TOKEN):
        self.integraciones[str(tenant)] = json.dumps({"bot_token": token})

    @asynccontextmanager
    async def acquire(self):
        yield Conexion(self)

    async def fetchrow(self, sql, *args):
        """`select current_user, session_user` del pool (la comprobación del rol)."""
        if self.falla_fetchrow is not None:
            raise self.falla_fetchrow
        return {"actual": self.rol, "sesion": self.rol}


class Conexion(TransaccionPorExecute):
    def __init__(self, mundo):
        self.m = mundo
        self.contexto = None

    async def execute(self, sql, *args):
        if sql.startswith("BEGIN"):
            self.m.tx_abiertas += 1
        elif sql in ("COMMIT", "ROLLBACK"):
            self.m.tx_abiertas -= 1
        if "set_config('app.current_tenant_id'" in sql:
            self.contexto = args[0]

    async def fetch(self, sql, *args):
        if "claim_notification_outbox" in sql:
            self.m.reclamos.append(args)
            if self.m.falla_reclamo:
                raise self.m.falla_reclamo.pop(0)
            entregadas, self.m.cola = self.m.cola[: args[0]], self.m.cola[args[0] :]
            return entregadas
        assert "resolve_outbox_delivery" in sql, sql
        assert self.contexto is not None, "resolve_outbox_delivery exige el contexto de tenant"
        if self.m.falla_resolver:
            raise RuntimeError("base caída")
        salida = []
        for d in self.m.destinos.get(args[0], []):
            entrega = self.m.entregas.get((args[0], d["clave"]))
            salida.append({**d, "entregado": d["entregado"] or (entrega is not None and entrega[0] == "sent")})
        return salida

    async def fetchval(self, sql, *args):
        assert self.contexto is not None, f"sin contexto de tenant: {sql}"
        if "registrar_entrega_outbox" in sql:
            self.m.entregas_llamadas.append((self.contexto, *args))
            if self.m.falla_registrar_entrega is not None:
                raise self.m.falla_registrar_entrega
            id_, clave, estado, error = args
            previo = self.m.entregas.get((id_, clave))
            if previo is not None and previo[0] == "sent":
                return False
            self.m.entregas[(id_, clave)] = (estado, error)
            return True
        if "complete_notification_outbox" in sql:
            if self.m.falla_cierre is not None:
                raise self.m.falla_cierre
            self.m.cierres.append((*args, self.contexto))
            return args[0] not in self.m.cierres_perdidos
        if "find_integration" in sql:
            return uuid.uuid4() if self.m.integraciones.get(self.contexto) is not None else None
        if "get_integration_secret" in sql:
            self.m.lecturas_de_secret.append(self.contexto)
            return self.m.integraciones[self.contexto]
        if "record_notification_attempt" in sql:
            if self.m.falla_registro_envio is not None:
                raise self.m.falla_registro_envio
            cliente, canal, plantilla, estado = args[:4]
            self.m.registros.append((self.contexto, cliente, canal, plantilla, estado))
            return uuid.uuid4()
        raise AssertionError(sql)


async def sin_espera(_):
    return None


class Esperas:
    """`esperar` que recuerda cuánto pidió esperar y no duerme."""

    def __init__(self):
        self.segundos = []

    async def __call__(self, segundos):
        self.segundos.append(segundos)


class Reportes:
    def __init__(self):
        self.items = []

    def __call__(self, descripcion, **etiquetas):
        self.items.append((descripcion, etiquetas))


class Telegram:
    """Bot API simulada: `estados` por chat (por defecto 200). Guarda cada pedido (token en la URL, cuerpo)."""

    def __init__(self, estados=None, error_de_red=None, mundo=None):
        self.estados = estados or {}
        self.error_de_red = error_de_red
        self.mundo = mundo
        self.pedidos = []
        self.cliente = httpx.AsyncClient(transport=httpx.MockTransport(self._responder))

    def _responder(self, req):
        if self.mundo is not None:
            self.mundo.http_con_tx.append(self.mundo.tx_abiertas)
        if self.error_de_red is not None:
            raise self.error_de_red
        cuerpo = json.loads(req.content)
        self.pedidos.append((str(req.url), cuerpo))
        estado = self.estados.get(cuerpo["chat_id"], 200)
        if estado == 200:
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(
            estado, json={"ok": False, "error_code": estado, "description": "descripcion del proveedor"}
        )

    @property
    def chats(self):
        return [c["chat_id"] for _, c in self.pedidos]


class CorreoSimulado:
    """Cliente de email que guarda lo enviado y falla con el `ErrorEmail` que se le indique por dirección."""

    def __init__(self, errores=None, mundo=None, error_siempre=None):
        self.errores = errores or {}
        self.error_siempre = error_siempre  # el mismo error para cualquier dirección
        self.mundo = mundo
        self.enviados = []

    async def enviar(self, destino_, asunto, texto, html=None):
        if self.mundo is not None:
            self.mundo.http_con_tx.append(self.mundo.tx_abiertas)
        self.enviados.append((destino_, asunto, texto))
        error = self.error_siempre or self.errores.get(destino_)
        if error is not None:
            raise error


def error_email(estado=None):
    return ErrorEmail(estado_http=estado)
