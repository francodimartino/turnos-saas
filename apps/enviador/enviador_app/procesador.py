"""Procesa una fila reclamada del outbox: resuelve sus destinatarios y envía por cada canal (ARCHITECTURE.md §6.4).

Una fila del cliente se envía a todos los canales verificados de la persona (Telegram y email); una del negocio, al
personal que el negocio configuró (email). Cada canal es independiente: uno que falla no impide los demás ni repite el
que ya salió. Antes de enviar se consultan las entregas ya registradas de la fila (`entregado`) y cada canal enviado
se registra con `registrar_entrega_outbox` justo después de salir, no al final de la fila: si el proceso muere a mitad
de fila, el reintento solo repite lo que no quedó registrado.

Cierre de la fila (misma política que el reescaneo del job de vencimiento, extendida a varios canales):
- todos los canales salieron: `sent`;
- algún canal falló por una causa transitoria (red, timeout, 429, 5xx, plazo agotado) y quedan reclamos
  (`max_intentos`): `pending`, con espera de `reintento_seg`; lo ya entregado no se repite, y un canal con fallo
  permanente (un 4xx distinto de 429, token revocado, sin integración) tampoco se reintenta: su `failed` en `entregas`
  lo excluye de las pasadas siguientes (`consultar_entregas_outbox`);
- sin reclamos disponibles, o solo con fallos permanentes: `sent` si al menos un canal entregó (el canal que falló
  queda `failed` en `entregas` y, en avisos al cliente, en `notifications_log`), `failed` si ninguno entregó
  (visible en el panel);
- sin a quién avisar (turno inexistente, tenant inactivo, ficha anonimizada, sin canal verificado ni email del
  negocio, plantilla desconocida): `failed` con el motivo, sin contar como fallo del servicio;
- la sincronización con Calendar no se envía acá: la fila no se cierra.

Tiempos y concurrencia: todo el envío de una fila tiene un tope (`plazo_fila_seg`, que `Config` exige mayor que el
peor caso de un canal con sus reintentos). Los canales que no terminaron se cortan: cada uno queda `failed` con el
código `plazo` en `entregas` y en `notifications_log` (nunca `pending`) y se trata como un fallo transitorio. Los
envíos de todas las filas de un lote comparten un tope de llamadas externas simultáneas (`concurrencia_envios`).

Las llamadas externas van fuera de toda transacción: cada operación de base (resolver, leer el token, registrar el
envío, registrar la entrega, cerrar) abre y cierra la suya con el contexto de tenant de la fila. Nada de lo que se
loguea o se reporta lleva email, texto, token ni identificador de canal: solo ids internos, el canal y un motivo.
"""

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import httpx
from platform_notifications import (
    BotAdapter,
    CanalVigilado,
    CompletadorOutboxFuncion,
    EnvioPedido,
    ErrorCredencial,
    ErrorEnvio,
    ObservadorDeIntentos,
    RegistradorFuncion,
    RegistradorIntentos,
    SinRegistro,
    adapter_telegram_del_tenant,
    enviar_con_reintentos,
)
from platform_secrets import crear_almacen
from platform_tenancy import tenant_tx
from turnos_negocio import asunto_aviso_turno, inicio_del_aviso, mensaje_aviso_turno

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


CODIGO_SIN_INTEGRACION = "sin_integracion"
CODIGO_PLAZO = "plazo"
_CODIGO_HTTP_PERMANENTE = re.compile(r"http_4\d\d")


def _codigo_corto(error: ErrorEnvio | None) -> str:
    if error is None:
        return "interno"
    return f"http_{error.estado_http}" if error.estado_http is not None else "red"


def es_codigo_permanente(codigo: str | None) -> bool:
    """El código corto que queda en `entregas` dice si reintentar no sirve: un 4xx distinto de 429 (el destinatario
    bloqueó al bot, una dirección rechazada, un token revocado) o un negocio sin integración de Telegram."""
    if codigo is None:
        return False
    return codigo == CODIGO_SIN_INTEGRACION or (
        _CODIGO_HTTP_PERMANENTE.fullmatch(codigo) is not None and codigo != "http_429"
    )


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
        plazo_fila_seg: float = 300.0,
        concurrencia_envios: int = 10,
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
        self._plazo_fila = plazo_fila_seg
        # Llamadas externas simultáneas de todo el lote: las filas se acotan por su cantidad, pero una fila puede
        # tener decenas de destinatarios y cada uno es un pedido a Telegram o a Resend.
        self._cupos_envio = asyncio.Semaphore(max(1, concurrencia_envios))

    # --- Base de datos: una transacción corta por operación, con el contexto de tenant de la fila -----------------

    async def _leer(self, fila: Any) -> tuple[list[Any], dict[str, tuple[str, str | None]]]:
        """Destinatarios de la fila (`resolve_outbox_delivery`) y lo ya registrado en `entregas`, en una transacción."""
        async with tenant_tx(self._pool, fila["tenant_id"]) as conn:
            destinos = list(await conn.fetch("select * from public.resolve_outbox_delivery($1)", fila["id"]))
            entregas = await conn.fetch("select * from public.consultar_entregas_outbox($1)", fila["id"])
        return destinos, {e["clave"]: (e["estado"], e["error"]) for e in entregas}

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

    async def cerrar_agotada(self, fila: Any) -> bool:
        """Cierre de una fila cuyo procesamiento cortó el bucle por plazo. En el último reclamo no hay otra pasada: se
        cierra según lo ya entregado (`sent` si algún canal salió, `failed` si ninguno), igual que la base al
        reclamarla de nuevo. Con reclamos disponibles no hace nada: la retoma otra pasada al vencer el arriendo."""
        if fila["attempts"] < self._max_intentos:
            return False
        try:
            async with tenant_tx(self._pool, fila["tenant_id"]) as conn:
                entregas = await conn.fetch("select * from public.consultar_entregas_outbox($1)", fila["id"])
        except Exception as error:  # noqa: BLE001 - sin poder leer, queda pending y lo resuelve el reclamo de la base
            log.warning("outbox_sin_cerrar", extra={"tenant_id": str(fila["tenant_id"]), "error": type(error).__name__})
            return False
        salio_alguno = any(e["estado"] == "sent" for e in entregas)
        return await self._cerrar(fila, "sent" if salio_alguno else "failed", CODIGO_PLAZO)

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
        destinos, entregas = await self._leer(fila)
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
            inicio=inicio_del_aviso(fila, turno),
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
        ya_entregados = [d for d in entregables if d["entregado"]]
        # un canal que ya falló de forma permanente no se reintenta nunca: su `failed` figura en `entregas`
        con_fallo_permanente = [
            d for d in entregables if not d["entregado"] and _fallo_permanente_registrado(entregas.get(d["clave"]))
        ]
        pendientes = [d for d in entregables if d not in ya_entregados and d not in con_fallo_permanente]
        asunto = asunto_aviso_turno(fila["template"]) or "Aviso de turno"
        resultados = await self._enviar_a_todos(fila, pendientes, texto, asunto, credenciales)
        return await self._cerrar_fila(fila, resultados, len(ya_entregados), len(con_fallo_permanente))

    async def _enviar_a_todos(
        self, fila: Any, pendientes: list[Any], texto: str, asunto: str, credenciales: CredencialesDelLote
    ) -> list[ResultadoCanal]:
        """Todos los canales pendientes de la fila a la vez (acotados por el tope de envíos del lote) y dentro del
        plazo de la fila: lo que no terminó se corta y cuenta como un fallo transitorio (`plazo`)."""
        if not pendientes:
            return []
        tareas = {
            asyncio.ensure_future(self._enviar_canal(fila, d, texto, asunto, credenciales)): d for d in pendientes
        }
        try:
            _, sin_terminar = await asyncio.wait(tareas, timeout=self._plazo_fila)
        except asyncio.CancelledError:  # el bucle cortó la fila: ningún canal queda corriendo solo
            for tarea in tareas:
                tarea.cancel()
            await asyncio.gather(
                *tareas, return_exceptions=True
            )  # cada canal cortado cierra su fila de `notifications_log`
            raise
        for tarea in sin_terminar:
            tarea.cancel()
        if sin_terminar:
            await asyncio.gather(*sin_terminar, return_exceptions=True)
            log.error(
                "outbox_fila_con_plazo_vencido",
                extra={
                    "tenant_id": str(fila["tenant_id"]),
                    "outbox_id": str(fila["id"]),
                    "fallidos": len(sin_terminar),
                },
            )
        resultados = []
        for tarea, d in tareas.items():
            if tarea in sin_terminar or tarea.cancelled():
                resultados.append(ResultadoCanal(d["channel"], d["clave"], "failed", "plazo_agotado", CODIGO_PLAZO))
            else:
                resultados.append(tarea.result())
        return resultados

    async def _cerrar_fila(
        self, fila: Any, resultados: list[ResultadoCanal], ya_entregados: int, con_fallo_permanente: int
    ) -> SalidaFila:
        fallidos = [r for r in resultados if r.estado != "sent"]
        entrego_alguno = ya_entregados > 0 or len(fallidos) < len(resultados)
        transitorios = [r for r in fallidos if not r.permanente]
        motivo = "; ".join(f"{r.canal}: {r.motivo}" for r in fallidos)[:200]
        if not motivo and con_fallo_permanente:
            motivo = "canales_con_fallo_permanente"
        if transitorios and fila["attempts"] < self._max_intentos:
            cerrada = await self._cerrar(fila, "pending", motivo or None, self._reintento_seg)
            tipo = "reprogramada"
        elif entrego_alguno:
            cerrada = await self._cerrar(fila, "sent", motivo or None)
            tipo = "enviada"
        else:
            cerrada = await self._cerrar(fila, "failed", motivo or None)
            tipo = "fallo"
        if not cerrada:
            return SalidaFila("cerrada", motivo or None, resultados)
        return SalidaFila(tipo, motivo or None, resultados)

    # --- Un canal --------------------------------------------------------------------------------------------------

    async def _enviar_canal(
        self, fila: Any, destino: Any, texto: str, asunto: str, credenciales: CredencialesDelLote
    ) -> ResultadoCanal:
        """Envía por un destinatario y registra su resultado. Solo lanza `CancelledError` (el plazo de la fila): un
        error de un canal no rompe a los demás."""
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
                return ResultadoCanal(canal, clave, "failed", sin_bot, CODIGO_SIN_INTEGRACION, permanente=True)
            adapter = real
        else:
            adapter = AdaptadorEmail(self._cliente_email, asunto)
        vigilado = CanalVigilado(adapter)
        cliente = fila["end_customer_id"]
        registrador: RegistradorIntentos = (
            SinRegistro() if cliente is None else RegistradorFuncion(lambda: tenant_tx(self._pool, tenant))
        )
        observador = ObservadorDeIntentos(registrador, ante_fallo=_avisar_registro_fallido(tenant))
        pedido = EnvioPedido(cliente, destino["channel_user_id"], texto, template=fila["template"])
        async with self._cupos_envio:
            try:
                await enviar_con_reintentos(vigilado, observador, pedido, backoff=self._backoff, esperar=self._esperar)
            except asyncio.CancelledError:
                # El plazo de la fila cortó el envío: su fila de `notifications_log` no puede quedar `pending` y el
                # canal queda `failed` (transitorio) en `entregas`; se reintenta en la pasada siguiente.
                await asyncio.shield(self._cerrar_cortado(fila, clave, observador))
                raise
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

    async def _cerrar_cortado(self, fila: Any, clave: str, observador: ObservadorDeIntentos) -> None:
        try:
            await observador.cerrar_fallido("plazo_agotado")
        except Exception as error:  # noqa: BLE001 - es una limpieza: no puede ocultar el corte
            log.warning(
                "registro_de_envio_fallido",
                extra={"tenant_id": str(fila["tenant_id"]), "error": type(error).__name__},
            )
        if observador.estado is not None:  # si ni siquiera empezó el envío, no hay nada que registrar
            await self._registrar_entrega(fila, clave, "failed", CODIGO_PLAZO)


def _fallo_permanente_registrado(entrega: tuple[str, str | None] | None) -> bool:
    return entrega is not None and entrega[0] == "failed" and es_codigo_permanente(entrega[1])


def _avisar_registro_fallido(tenant: Any) -> Callable[[Exception, dict], None]:
    def avisar(error: Exception, datos: dict) -> None:
        log.warning(
            "registro_de_envio_fallido",
            extra={"tenant_id": str(tenant), "canal": datos["channel"], "error": type(error).__name__},
        )

    return avisar
