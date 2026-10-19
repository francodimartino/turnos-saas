"""Limpieza de Google Calendar después de anonimizar a un cliente (ARCHITECTURE.md §3.5).

Corre DESPUÉS del commit de la anonimización (los eventos de Google no se pueden revertir y la base no puede
depender de una API externa). Los eventos a limpiar salen de `calendar_scrub_targets`, que se consulta con el
contexto de tenant del bot. Cada evento que no se pudo limpiar queda en `audit_log` como
`calendar_scrub_failed` con `{'event_id', 'motivo'}` y nada más: sin teléfono, nombre ni identificador de canal.
Los `pending` futuros ya los canceló la función SQL `anonymize_end_customer`; si el evento quedó en Google
(el profesional lo ve igual), el borrado de datos personales del evento es lo que importa acá.
"""

import logging
import uuid
from collections.abc import Callable
from typing import Any

import httpx
from platform_audit import registrar
from platform_calendar import ConfigGoogle, ErrorCalendar, ErrorConfiguracionCalendar, crear_calendario
from platform_secrets import crear_almacen
from platform_tenancy import tenant_tx

from bot_app.despachador import AccionPosterior, Recursos

logger = logging.getLogger("bot.identidad.calendario")

ACCION_AUDIT = "calendar_scrub_failed"
MOTIVO_SIN_INTEGRACION = "sin_integracion"
MOTIVO_NO_CONFIGURADO = "calendario_no_configurado"
MOTIVO_INESPERADO = "error_inesperado"

_OBJETIVOS = "select integration_id, google_calendar_event_id from public.calendar_scrub_targets($1, $2)"


def crear_fabrica_calendario(config: ConfigGoogle) -> Callable[[Any, httpx.AsyncClient], Any]:
    """Fábrica del servicio real de Calendar con la configuración de Google ya leída (una sola vez al arrancar)."""

    def fabrica(conn: Any, cliente_http: httpx.AsyncClient) -> Any:
        return crear_calendario(config, crear_almacen(conn), cliente_http)

    return fabrica


def fabrica_calendario_deshabilitada(conn: Any, cliente_http: Any) -> Any:
    """Calendar sin configurar: toda operación lo trata como 'no configurado' (la reserva sigue igual)."""
    raise ErrorConfiguracionCalendar("calendario no configurado")


def fabrica_calendario_desde_entorno() -> Callable[[Any, Any], Any]:
    """Lee y valida la configuración de Google del entorno UNA vez, al arrancar.

    Sin ninguna variable, devuelve la fábrica deshabilitada (Calendar apagado). Con la configuración a medias o
    insegura lanza `ErrorConfiguracionCalendar` (el bot no arranca) con un mensaje que nombra la variable.
    """
    config = ConfigGoogle.opcional_desde_entorno(requiere_state=False)
    if config is None:
        logger.warning("calendario_deshabilitado: Google sin configurar")
        return fabrica_calendario_deshabilitada
    return crear_fabrica_calendario(config)


async def _auditar(pool: Any, tenant_id: uuid.UUID, fallidos: list[tuple[str, str]]) -> None:
    async with tenant_tx(pool, tenant_id) as conn:
        for event_id, motivo in fallidos:
            await registrar(conn, ACCION_AUDIT, metadata={"event_id": event_id, "motivo": motivo})


async def _limpiar(recursos: Recursos, tenant_id: uuid.UUID, objetivos: list[Any]) -> list[tuple[str, str]]:
    """Limpia los eventos con integración y devuelve `(event_id, motivo)` de los que fallaron."""
    fallidos: list[tuple[str, str]] = []
    pares: list[tuple[uuid.UUID, str]] = []
    for fila in objetivos:
        if fila["integration_id"] is None:
            fallidos.append((fila["google_calendar_event_id"], MOTIVO_SIN_INTEGRACION))
        else:
            pares.append((fila["integration_id"], fila["google_calendar_event_id"]))
    if not pares:
        return fallidos
    fabrica = recursos.fabrica_calendario or fabrica_calendario_deshabilitada
    propio = recursos.cliente_http is None
    cliente = httpx.AsyncClient(timeout=15.0) if propio else recursos.cliente_http
    try:
        # El almacén de secretos usa la conexión: se mantiene la transacción con el contexto de tenant abierta.
        async with tenant_tx(recursos.pool, tenant_id) as conn:
            try:
                servicio = fabrica(conn, cliente)
            except ErrorCalendar:
                return fallidos + [(event_id, MOTIVO_NO_CONFIGURADO) for _, event_id in pares]
            for no_limpiado in await servicio.limpiar_eventos(pares):
                fallidos.append((no_limpiado.event_id, no_limpiado.motivo))
    finally:
        if propio:
            await cliente.aclose()
    return fallidos


def limpiar_calendario_de(tenant_id: uuid.UUID, end_customer_id: uuid.UUID) -> AccionPosterior:
    """Acción para `ctx.despues_del_commit`: limpia los eventos de Calendar de ese cliente ya anonimizado."""

    async def accion(recursos: Recursos) -> None:
        async with tenant_tx(recursos.pool, tenant_id) as conn:
            objetivos = list(await conn.fetch(_OBJETIVOS, tenant_id, end_customer_id))
        if not objetivos:
            return
        try:
            fallidos = await _limpiar(recursos, tenant_id, objetivos)
        except Exception:  # noqa: BLE001 - toda falla inesperada deja constancia por evento, sin datos del cliente
            logger.exception("error_limpiando_calendario")
            fallidos = [(fila["google_calendar_event_id"], MOTIVO_INESPERADO) for fila in objetivos]
        if fallidos:
            await _auditar(recursos.pool, tenant_id, fallidos)

    return accion
