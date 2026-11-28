"""Procesa una fila reclamada del outbox: resuelve sus destinatarios y envía por cada canal (ARCHITECTURE.md §6.4).

Una fila del cliente se envía a todos los canales verificados de la persona (Telegram y email); una del negocio, al
personal que el negocio configuró (email). Cada canal es independiente: uno que falla no impide los demás ni repite el
que ya salió. Antes de enviar se consultan las entregas ya registradas de la fila (`entregado`) y cada canal enviado
se registra con `registrar_entrega_outbox` justo después de salir, no al final de la fila: si el proceso muere a mitad
de fila, el reintento solo repite lo que no quedó registrado.

Cierre de la fila (misma política que el reescaneo del job de vencimiento, extendida a varios canales):
- todos los canales salieron: `sent`;
- algún canal falló por una causa transitoria (red, timeout, 429, 5xx) y quedan reclamos (`max_intentos`): `pending`,
  con espera de `reintento_seg`; lo ya entregado no se repite;
- sin reclamos disponibles, o con fallos permanentes (un 4xx distinto de 429, token revocado, sin integración): `sent`
  si al menos un canal entregó (el canal que falló queda `failed` en `entregas` y, en avisos al cliente, en
  `notifications_log`), `failed` si ninguno entregó (visible en el panel);
- sin a quién avisar (turno inexistente, tenant inactivo, ficha anonimizada, sin canal verificado ni email del
  negocio, plantilla desconocida): `failed` con el motivo, sin contar como fallo del servicio;
- la sincronización con Calendar no se envía acá: la fila no se cierra.

Las llamadas externas van fuera de toda transacción: cada operación de base (resolver, leer el token, registrar el
envío, registrar la entrega, cerrar) abre y cierra la suya con el contexto de tenant de la fila. Nada de lo que se
loguea o se reporta lleva email, texto, token ni identificador de canal: solo ids internos, el canal y un motivo.
"""

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

import httpx
from platform_notifications import (
    BotAdapter,
    CompletadorOutboxFuncion,
    EnvioPedido,
    ErrorCredencial,
    ErrorEnvio,
    RegistradorFuncion,
    RegistradorIntentos,
    adapter_telegram_del_tenant,
    enviar_con_reintentos,
)
from platform_secrets import crear_almacen
from platform_tenancy import tenant_tx
from turnos_negocio import asunto_aviso_turno, mensaje_aviso_turno

from enviador_app.canales import CANAL_TELEGRAM, CANALES_ENVIABLES, AdaptadorEmail, ClienteDeEmail

log = logging.getLogger("enviador_app.procesador")

# Tope de destinatarios de una fila. `notification_outbox.entregas` tiene un CHECK de 16000 caracteres y cada
# destinatario ocupa su clave (hasta 45) más su resultado (hasta unos 150 con el código corto de error): 100 caben con
# margen. Los que excedan el tope no se envían ni se registran: se reporta.
MAX_DESTINATARIOS_POR_FILA = 100
MAX_CODIGO_ERROR = 24
Reportar = Callable[..., None]


@dataclass(frozen=True)
class ResultadoCanal:
    """Qué pasó con un destinatario: `sent` o `failed`. `permanente`: reintentar no lo arregla."""

    canal: str
    clave: str
    estado: str
    motivo: str | None = None  # texto del proveedor (sin el mensaje enviado ni la dirección), para last_error
    codigo: str | None = None  # código corto del fallo, lo que se guarda en `entregas`
    permanente: bool = False
    registrada: bool = True  # la entrega quedó en `entregas`


@dataclass
class SalidaFila:
    """Qué pasó con una fila: `enviada`, `reprogramada`, `descartada` (sin a quién avisar), `cerrada` (otro proceso la
    cerró antes o el cierre falló), `omitida` (no es de este servicio: queda `pending`) o `fallo`."""

    tipo: str
    motivo: str | None = None  # el texto que queda en `last_error` de la fila: puede traer el mensaje del proveedor
    canales: list[ResultadoCanal] = field(default_factory=list)

    @property
    def etiqueta(self) -> str | None:
        """Lo que se loguea y se reporta: los códigos cortos de los canales que fallaron (`telegram:http_403`) o el
        motivo fijo de un descarte. Nunca el texto del proveedor, que podría repetir datos del pedido."""
        fallidos = [f"{r.canal}:{r.codigo}" for r in self.canales if r.estado != "sent"]
        return ", ".join(fallidos) or self.motivo


@dataclass
class _CredencialTelegram:
    candado: asyncio.Lock = field(default_factory=asyncio.Lock)
    adapter: BotAdapter | None = None
    error: str | None = None  # motivo (sin secretos) si el tenant no tiene un bot utilizable


class CredencialesDelLote:
    """Un adapter de Telegram por tenant y por lote: las filas del mismo tenant comparten la lectura del token, y el
    lote siguiente lo vuelve a leer (una rotación del token se toma sin reiniciar el servicio)."""

    def __init__(self) -> None:
        self._por_tenant: dict[str, _CredencialTelegram] = {}

    def de(self, tenant: Any) -> _CredencialTelegram:
        return self._por_tenant.setdefault(str(tenant), _CredencialTelegram())


class _SinRegistro(RegistradorIntentos):
    """Un aviso al negocio no tiene ficha de cliente: `record_notification_attempt` la exige, así que no se registra."""

    async def registrar(
        self, *, end_customer_id, channel, template, status, retry_count=0, last_error=None, notification_id=None
    ):
        return notification_id or uuid.uuid4()


class _Observador(RegistradorIntentos):
    """Delega en el registrador real y recuerda el estado final. Un fallo al escribir `notifications_log` no impide el
    envío ni lo hace repetir: se loguea y el envío sigue."""

    def __init__(self, real: RegistradorIntentos, tenant: Any) -> None:
        self._real = real
        self._tenant = tenant
        self.estado: str | None = None
        self.ultimo_error: str | None = None

    async def registrar(self, **datos):
        self.estado = datos["status"]
        self.ultimo_error = datos.get("last_error")
        try:
            return await self._real.registrar(**datos)
        except Exception as error:  # noqa: BLE001 - el envío no depende de su registro
            log.warning(
                "registro_de_envio_fallido",
                extra={"tenant_id": str(self._tenant), "canal": datos["channel"], "error": type(error).__name__},
            )
            return datos.get("notification_id") or uuid.uuid4()


class _AdapterVigilado(BotAdapter):
    """Delega en el adapter real y recuerda el último `ErrorEnvio` (para saber si el fallo es permanente)."""

    def __init__(self, real: BotAdapter) -> None:
        self._real = real
        self.canal = real.canal
        self.ultimo_error: ErrorEnvio | None = None

    async def _vigilar(self, llamada: Awaitable[None]) -> None:
        try:
            await llamada
        except ErrorEnvio as error:
            self.ultimo_error = error
            raise

    async def send_message(self, destinatario: str, texto: str) -> None:
        await self._vigilar(self._real.send_message(destinatario, texto))

    async def send_buttons(self, destinatario, texto, botones) -> None:
        await self._vigilar(self._real.send_buttons(destinatario, texto, botones))

    @staticmethod
    def parsear_update(payload: Any):
        return None


def _parametros(fila: Any) -> dict[str, Any]:
    crudo = fila["params"]
    datos = json.loads(crudo) if isinstance(crudo, str) else crudo
    return datos if isinstance(datos, dict) else {}


def _inicio_aviso(fila: Any, destino: Any) -> datetime:
    """El inicio con que se encoló el aviso (un turno movido ya no empieza donde empezaba); si falta, el del turno."""
    try:
        return datetime.fromisoformat(str(_parametros(fila)["inicio"]))
    except (KeyError, ValueError):
        return destino["starts_at"]


def _codigo_corto(error: ErrorEnvio | None) -> str:
    if error is None:
        return "interno"
    return f"http_{error.estado_http}" if error.estado_http is not None else "red"


class ProcesadorOutbox:
    def __init__(
        self,
        pool: Any,
        *,
        cliente_http: httpx.AsyncClient,
        cliente_email: ClienteDeEmail,
        base_url_telegram: str,
        backoff: Sequence[float],
        esperar: Callable[[float], Awaitable[None]],
        max_intentos: int,
        reintento_seg: int,
        reportar: Reportar,
    ) -> None:
        self._pool = pool
        self._cliente_http = cliente_http
        self._cliente_email = cliente_email
        self._base_url_telegram = base_url_telegram
        self._backoff = backoff
        self._esperar = esperar
        self._max_intentos = max_intentos
        self._reintento_seg = reintento_seg
        self._reportar = reportar

    # --- Base de datos: una transacción corta por operación, con el contexto de tenant de la fila -----------------

    async def _destinos(self, fila: Any) -> list[Any]:
        async with tenant_tx(self._pool, fila["tenant_id"]) as conn:
            return list(await conn.fetch("select * from public.resolve_outbox_delivery($1)", fila["id"]))

    def _completador(self, tenant: Any) -> CompletadorOutboxFuncion:
        return CompletadorOutboxFuncion(lambda: tenant_tx(self._pool, tenant))

    async def _cerrar(self, fila: Any, status: str, motivo: str | None, reintento: int | None = None) -> bool:
        """Cierra o reprograma la fila sin propagar fallos. `False` si no se pudo o si ya no estaba `pending`."""
        try:
            return bool(await self._completador(fila["tenant_id"]).completar(fila["id"], status, motivo, reintento))
        except Exception as error:  # noqa: BLE001 - queda pending: otra instancia la retoma al vencer el arriendo
            log.warning("outbox_sin_cerrar", extra={"tenant_id": str(fila["tenant_id"]), "error": type(error).__name__})
            return False

    async def liberar(self, fila: Any) -> bool:
        """Devuelve la fila a `pending` para que otra instancia la tome enseguida (el apagado no la deja arrendada)."""
        return await self._cerrar(fila, "pending", "enviador_detenido", 0)

    async def _registrar_entrega(self, fila: Any, clave: str, estado: str, codigo: str | None) -> bool:
        try:
            async with tenant_tx(self._pool, fila["tenant_id"]) as conn:
                return bool(
                    await conn.fetchval(
                        "select public.registrar_entrega_outbox($1, $2, $3, $4)", fila["id"], clave, estado, codigo
                    )
                )
        except Exception as error:  # noqa: BLE001 - el envío ya ocurrió: el peor caso es un duplicado en el reintento
            log.warning(
                "entrega_sin_registrar",
                extra={
                    "tenant_id": str(fila["tenant_id"]),
                    "outbox_id": str(fila["id"]),
                    "error": type(error).__name__,
                },
            )
            return False

    async def _adapter_telegram(self, tenant: Any, estado: _CredencialTelegram) -> tuple[BotAdapter | None, str | None]:
        async with estado.candado:
            if estado.adapter is None and estado.error is None:
                try:
                    async with tenant_tx(self._pool, tenant) as conn:  # solo para leer el token
                        estado.adapter = await adapter_telegram_del_tenant(
                            crear_almacen(conn), self._cliente_http, base_url=self._base_url_telegram
                        )
                except ErrorCredencial as error:
                    estado.error = type(error).__name__
            return estado.adapter, estado.error

    # --- Una fila --------------------------------------------------------------------------------------------------

    async def _descartar(self, fila: Any, motivo: str) -> SalidaFila:
        return SalidaFila("descartada" if await self._cerrar(fila, "failed", motivo) else "cerrada", motivo)

    async def procesar(self, fila: Any, credenciales: CredencialesDelLote) -> SalidaFila:
        destinos = await self._destinos(fila)
        if not destinos:
            return await self._descartar(fila, "turno_inexistente")  # sin turno, o la fila ya no estaba `pending`
        turno = destinos[0]
        if not turno["tenant_active"]:
            return await self._descartar(fila, "tenant_inactivo")
        if turno["anonimizada"]:
            return await self._descartar(fila, "cliente_sin_destinatario")
        entregables = [d for d in destinos if d["channel"] in CANALES_ENVIABLES]
        if not entregables:
            if any(d["channel"] == "calendar" for d in destinos):
                return SalidaFila("omitida", "sincronizacion_calendar")
            return await self._descartar(fila, "sin_destinatarios")
        texto = mensaje_aviso_turno(
            fila["template"],
            servicio=turno["service_name"],
            profesional=turno["professional_name"],
            inicio=_inicio_aviso(fila, turno),
            vence=turno["pending_expires_at"],
        )
        if texto is None:
            return await self._descartar(fila, "plantilla_desconocida")
        if len(entregables) > MAX_DESTINATARIOS_POR_FILA:
            self._reportar(
                "un aviso tiene más destinatarios de los que admite el registro de entregas",
                motivo="destinatarios_excedidos",
                tenant_id=fila["tenant_id"],
            )
            log.error(
                "destinatarios_excedidos",
                extra={
                    "tenant_id": str(fila["tenant_id"]),
                    "outbox_id": str(fila["id"]),
                    "motivo": "destinatarios_excedidos",
                },
            )
            entregables = entregables[:MAX_DESTINATARIOS_POR_FILA]
        pendientes = [d for d in entregables if not d["entregado"]]
        asunto = asunto_aviso_turno(fila["template"]) or "Aviso de turno"
        resultados = list(
            await asyncio.gather(*(self._enviar_canal(fila, d, texto, asunto, credenciales) for d in pendientes))
        )
        return await self._cerrar_fila(fila, resultados, ya_entregados=len(entregables) - len(pendientes))

    async def _cerrar_fila(self, fila: Any, resultados: list[ResultadoCanal], ya_entregados: int) -> SalidaFila:
        fallidos = [r for r in resultados if r.estado != "sent"]
        entrego_alguno = ya_entregados > 0 or len(fallidos) < len(resultados)
        transitorios = [r for r in fallidos if not r.permanente]
        motivo = "; ".join(f"{r.canal}: {r.motivo}" for r in fallidos)[:200] or None
        if transitorios and fila["attempts"] < self._max_intentos:
            cerrada = await self._cerrar(fila, "pending", motivo, self._reintento_seg)
            tipo = "reprogramada"
        elif entrego_alguno:
            cerrada = await self._cerrar(fila, "sent", motivo)
            tipo = "enviada"
        else:
            cerrada = await self._cerrar(fila, "failed", motivo)
            tipo = "fallo"
        if not cerrada:
            return SalidaFila("cerrada", motivo, resultados)
        return SalidaFila(tipo, motivo, resultados)

    # --- Un canal --------------------------------------------------------------------------------------------------

    async def _enviar_canal(
        self, fila: Any, destino: Any, texto: str, asunto: str, credenciales: CredencialesDelLote
    ) -> ResultadoCanal:
        """Envía por un destinatario y registra su resultado. Nunca lanza: un canal no rompe a los demás."""
        canal, clave = destino["channel"], destino["clave"]
        try:
            resultado = await self._intentar(fila, destino, texto, asunto, credenciales)
        except Exception as error:  # noqa: BLE001 - un error inesperado de un canal no corta a los demás
            resultado = ResultadoCanal(
                canal, clave, "failed", f"error_inesperado: {type(error).__name__}", "interno", permanente=False
            )
        registrada = await self._registrar_entrega(fila, clave, resultado.estado, resultado.codigo)
        resultado = replace(resultado, registrada=registrada)
        log.log(
            logging.INFO if resultado.estado == "sent" else logging.WARNING,
            "entrega_canal",
            extra={
                "tenant_id": str(fila["tenant_id"]),
                "outbox_id": str(fila["id"]),
                "canal": canal,
                "resultado": resultado.estado,
                "motivo": resultado.codigo,
            },
        )
        return resultado

    async def _intentar(
        self, fila: Any, destino: Any, texto: str, asunto: str, credenciales: CredencialesDelLote
    ) -> ResultadoCanal:
        canal, clave, tenant = destino["channel"], destino["clave"], fila["tenant_id"]
        adapter: BotAdapter
        if canal == CANAL_TELEGRAM:
            real, sin_bot = await self._adapter_telegram(tenant, credenciales.de(tenant))
            if real is None:
                return ResultadoCanal(canal, clave, "failed", sin_bot, "sin_integracion", permanente=True)
            adapter = real
        else:
            adapter = AdaptadorEmail(self._cliente_email, asunto)
        vigilado = _AdapterVigilado(adapter)
        cliente = fila["end_customer_id"]
        registrador: RegistradorIntentos = (
            _SinRegistro() if cliente is None else RegistradorFuncion(lambda: tenant_tx(self._pool, tenant))
        )
        observador = _Observador(registrador, tenant)
        pedido = EnvioPedido(cliente, destino["channel_user_id"], texto, template=fila["template"])
        await enviar_con_reintentos(vigilado, observador, pedido, backoff=self._backoff, esperar=self._esperar)
        if observador.estado == "sent":
            return ResultadoCanal(canal, clave, "sent")
        error = vigilado.ultimo_error
        return ResultadoCanal(
            canal,
            clave,
            "failed",
            (observador.ultimo_error or "envio_fallido")[:200],
            _codigo_corto(error)[:MAX_CODIGO_ERROR],
            permanente=error is not None and not error.reintentable,
        )
