"""Bucle del enviador: reclama el outbox cada pocos segundos y procesa las filas en paralelo (ARCHITECTURE.md §6.4).

Cada ciclo reclama un lote con `claim_notification_outbox` (SKIP LOCKED y arriendo: dos instancias, o el job de
vencimiento, nunca reciben la misma fila a la vez; el reclamo cuenta un intento y, agotados `max_intentos`, la base
deja la fila `failed`). Las filas del lote se procesan hasta `concurrencia` a la vez, cada una con su plazo
(`plazo_fila`): si vence, la fila queda `pending` y la retoma otra pasada al terminar el arriendo.

Cadencia: con trabajo, el siguiente reclamo es inmediato si el lote vino lleno y a los `intervalo` segundos si no; sin
trabajo (o con la base caída) la espera se duplica hasta `espera_maxima`. Ningún error de un ciclo ni de una fila corta
el bucle: se cuenta, se loguea y se reporta sin datos personales (solo `tenant_id` y un motivo).

Apagado (SIGTERM, a través de `detener`): no se reclama más; a las filas en curso se les espera hasta
`plazo_apagado`; las que no terminaron, y las del lote que todavía no empezaron, se devuelven a `pending` para que
otra instancia las tome enseguida. Lo ya entregado figura en `entregas` y no se repite.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from enviador_app.procesador import CredencialesDelLote, ProcesadorOutbox, SalidaFila

log = logging.getLogger("enviador_app")

Reportar = Callable[..., None]
Dormir = Callable[[asyncio.Event, float], Awaitable[None]]

_SQL_RECLAMAR = "select * from public.claim_notification_outbox($1, $2, $3)"
_MOTIVO_PLAZO = "plazo_agotado"


async def dormir_o_detener(detener: asyncio.Event, segundos: float) -> None:
    """Espera `segundos` o hasta que se pida detener, lo que ocurra primero."""
    try:
        await asyncio.wait_for(detener.wait(), segundos)
    except asyncio.TimeoutError:  # noqa: UP041 - en 3.10 no es el mismo tipo que TimeoutError
        return


@dataclass
class Contadores:
    """Acumulados desde que arrancó el proceso (solo números: nada de datos del cliente)."""

    reclamadas: int = 0
    enviadas: int = 0
    reprogramadas: int = 0
    descartadas: int = 0
    cerradas: int = 0
    omitidas: int = 0
    liberadas: int = 0
    fallos: int = 0
    por_canal: dict[str, dict[str, int]] = field(default_factory=dict)  # canal -> {"sent": n, "failed": m}

    def canal(self, canal: str, estado: str) -> None:
        cuenta = self.por_canal.setdefault(canal, {"sent": 0, "failed": 0})
        cuenta[estado] = cuenta.get(estado, 0) + 1


class Enviador:
    def __init__(
        self,
        pool: Any,
        procesador: ProcesadorOutbox,
        *,
        lote: int,
        concurrencia: int,
        arriendo_seg: int,
        max_intentos: int,
        plazo_fila_seg: float,
        plazo_apagado_seg: float,
        intervalo_seg: float,
        espera_maxima_seg: float,
        reportar: Reportar,
        dormir: Dormir = dormir_o_detener,
    ) -> None:
        self._pool = pool
        self._procesador = procesador
        self._lote = lote
        self._concurrencia = max(1, concurrencia)
        self._arriendo = arriendo_seg
        self._max_intentos = max_intentos
        self._plazo_fila = plazo_fila_seg
        self._plazo_apagado = plazo_apagado_seg
        self._intervalo = intervalo_seg
        self._espera_maxima = espera_maxima_seg
        self._reportar = reportar
        self._dormir = dormir
        self._en_fallo = False
        self.contadores = Contadores()
        self.ultimo_ciclo: float | None = None  # `time.monotonic()` de la última vuelta del bucle (para `/health`)

    async def correr(self, detener: asyncio.Event) -> None:
        """Procesa el outbox hasta que se pida detener. No lanza: lo que falla se cuenta y se reintenta."""
        log.info("enviador_iniciado")
        vacios = 0
        while not detener.is_set():
            try:
                reclamadas = await self.ciclo(detener)
                if self._en_fallo:
                    self._en_fallo = False
                    log.info("reclamo_recuperado")
            except Exception as error:  # noqa: BLE001 - sin reclamo no hay trabajo, pero el servicio sigue vivo
                reclamadas = 0
                self._fallo_del_ciclo(error)
            self.ultimo_ciclo = time.monotonic()
            if reclamadas >= self._lote:
                continue  # el lote vino lleno: probablemente hay más
            if reclamadas > 0:
                vacios, espera = 0, self._intervalo
            else:
                espera = min(self._intervalo * 2 ** min(vacios, 16), self._espera_maxima)
                vacios += 1
            await self._dormir(detener, espera)
        log.info("enviador_detenido")

    def _fallo_del_ciclo(self, error: Exception) -> None:
        log.error("ciclo_fallido", extra={"motivo": type(error).__name__})
        if not self._en_fallo:  # un solo reporte por racha: una base caída no llena Sentry
            self._en_fallo = True
            self._reportar("no se pudo reclamar el outbox de avisos", motivo=type(error).__name__)

    async def ciclo(self, detener: asyncio.Event) -> int:
        """Un reclamo y el procesamiento de lo reclamado. Devuelve cuántas filas se reclamaron."""
        async with self._pool.acquire() as conn:  # el reclamo es una sola sentencia: la conexión no queda retenida
            filas = list(await conn.fetch(_SQL_RECLAMAR, self._lote, self._arriendo, self._max_intentos))
        if not filas:
            return 0
        self.contadores.reclamadas += len(filas)
        antes = _copiar(self.contadores)
        await self._procesar_lote(filas, detener)
        log.info("lote_procesado", extra=_diferencia(antes, self.contadores) | {"reclamadas": len(filas)})
        return len(filas)

    async def _procesar_lote(self, filas: list[Any], detener: asyncio.Event) -> None:
        credenciales = CredencialesDelLote()
        cupos = asyncio.Semaphore(self._concurrencia)
        iniciadas: set[Any] = set()

        async def una(fila: Any) -> SalidaFila | None:
            async with cupos:
                if detener.is_set():
                    return None  # el apagado empezó antes de que le tocara: se devuelve sin tocarla
                iniciadas.add(fila["id"])
                return await asyncio.wait_for(self._procesador.procesar(fila, credenciales), self._plazo_fila)

        tareas: dict[asyncio.Future, Any] = {asyncio.ensure_future(una(f)): f for f in filas}
        espera_detener = asyncio.ensure_future(detener.wait())
        pendientes: set[asyncio.Future] = set(tareas)
        try:
            while pendientes and not espera_detener.done():
                hechas, _ = await asyncio.wait(pendientes | {espera_detener}, return_when=asyncio.FIRST_COMPLETED)
                pendientes -= hechas
            if pendientes:  # apagado a mitad de lote
                _, sin_terminar = await asyncio.wait(pendientes, timeout=self._plazo_apagado)
                for tarea in sin_terminar:
                    tarea.cancel()
                await asyncio.gather(*sin_terminar, return_exceptions=True)
        finally:
            espera_detener.cancel()
            for tarea in tareas:
                if not tarea.done():
                    tarea.cancel()  # cancelación externa: nada queda corriendo
        await self._cerrar_lote(tareas, iniciadas)

    async def _cerrar_lote(self, tareas: dict[asyncio.Future, Any], iniciadas: set[Any]) -> None:
        a_liberar: list[Any] = []
        for tarea, fila in tareas.items():
            etiquetas = {"tenant_id": str(fila["tenant_id"]), "outbox_id": str(fila["id"])}
            if tarea.cancelled() or (tarea.exception() is None and tarea.result() is None):
                a_liberar.append(fila)
                continue
            excepcion = tarea.exception()
            if isinstance(excepcion, asyncio.TimeoutError):
                self._fallo_de_fila(fila, _MOTIVO_PLAZO, "outbox_fila_plazo_agotado")
            elif excepcion is not None:
                self._fallo_de_fila(fila, f"error_inesperado: {type(excepcion).__name__}", "outbox_fallo")
            else:
                self._contar(tarea.result(), etiquetas)
        if a_liberar:
            await self._liberar(a_liberar)

    def _fallo_de_fila(self, fila: Any, motivo: str, evento: str) -> None:
        self.contadores.fallos += 1
        self._reportar("falló un aviso del outbox", motivo=motivo, tenant_id=fila["tenant_id"])
        log.error(evento, extra={"tenant_id": str(fila["tenant_id"]), "outbox_id": str(fila["id"]), "motivo": motivo})

    def _contar(self, salida: SalidaFila, etiquetas: dict[str, str]) -> None:
        c = self.contadores
        for resultado in salida.canales:
            c.canal(resultado.canal, "sent" if resultado.estado == "sent" else "failed")
        extra = {**etiquetas, "motivo": salida.etiqueta}
        if salida.tipo == "enviada":
            c.enviadas += 1
            log.info("outbox_enviada", extra=etiquetas)
        elif salida.tipo == "reprogramada":
            c.reprogramadas += 1
            log.warning("outbox_reprogramada", extra=extra)
        elif salida.tipo == "descartada":
            c.descartadas += 1
            log.warning("outbox_descartada", extra=extra)
        elif salida.tipo == "cerrada":
            c.cerradas += 1
            log.warning("outbox_cerrada", extra=extra)
        elif salida.tipo == "omitida":
            c.omitidas += 1
            log.warning("outbox_omitida", extra=extra)
        else:
            c.fallos += 1
            self._reportar("falló un aviso del outbox", motivo=salida.etiqueta, tenant_id=etiquetas["tenant_id"])
            log.error("outbox_fallo", extra=extra)

    async def _liberar(self, filas: list[Any]) -> None:
        """Devuelve a `pending` (disponibles ya) las filas que el apagado dejó sin terminar. Sin propagar fallos."""
        try:
            liberadas = await asyncio.wait_for(
                asyncio.gather(*(self._procesador.liberar(f) for f in filas), return_exceptions=True), _PLAZO_LIBERAR
            )
        except asyncio.TimeoutError:  # noqa: UP041
            liberadas = []
        devueltas = sum(1 for ok in liberadas if ok is True)
        self.contadores.liberadas += devueltas
        log.warning("outbox_liberadas", extra={"liberadas": devueltas, "pendientes": len(filas)})


# Tope para devolver las filas del apagado: Render da unos segundos entre SIGTERM y SIGKILL.
_PLAZO_LIBERAR = 5.0


def _copiar(c: Contadores) -> dict[str, int]:
    return {k: v for k, v in vars(c).items() if isinstance(v, int)}


def _diferencia(antes: dict[str, int], c: Contadores) -> dict[str, int]:
    return {k: v - antes[k] for k, v in _copiar(c).items() if k != "reclamadas"}
