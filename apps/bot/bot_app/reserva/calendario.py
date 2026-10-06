"""Evento de Google Calendar de un turno que nació `confirmed` (ARCHITECTURE.md §7.1).

Corre DESPUÉS del commit de la reserva (`ctx.despues_del_commit`): una API externa no puede ser parte de la
transacción ni hacer fallar la reserva. Un `pending` nunca llega acá (solo se agenda para `auto`) y, por las
dudas, se vuelve a leer el estado del booking: solo un `confirmed` sin evento se sincroniza. Si Calendar no está
configurado, el profesional no conectó el suyo o Google falla, la reserva queda igual y solo se deja un log sin
datos del cliente.
"""

import logging
import uuid

import httpx
from platform_calendar import AccionSync, BookingCalendario, ErrorCalendar, ErrorConfiguracionCalendar
from platform_tenancy import tenant_tx

from bot_app.despachador import AccionPosterior, Recursos
from bot_app.identidad.calendario import fabrica_calendario_por_defecto

logger = logging.getLogger("bot.reserva.calendario")

_LEER_BOOKING = """
select b.id, b.status, b.professional_id, b.google_calendar_event_id,
       lower(b.time_range) as inicio, upper(b.time_range) as fin,
       s.name as servicio, ec.name as cliente, ec.phone as telefono
from public.bookings b
join public.services s on s.id = b.service_id
join public.end_customers ec on ec.id = b.end_customer_id
where b.id = $1 and b.tenant_id = $2
"""

# `is null`: si otra sincronización ya guardó un evento, no se pisa.
_GUARDAR_EVENTO = (
    "update public.bookings set google_calendar_event_id = $3 "
    "where id = $1 and tenant_id = $2 and google_calendar_event_id is null"
)


async def _sincronizar(recursos: Recursos, tenant_id: uuid.UUID, booking_id: uuid.UUID, cliente_http) -> None:
    fabrica = recursos.fabrica_calendario or fabrica_calendario_por_defecto
    # El almacén de secretos usa la conexión: la transacción con el contexto de tenant sigue abierta.
    async with tenant_tx(recursos.pool, tenant_id) as conn:
        fila = await conn.fetchrow(_LEER_BOOKING, booking_id, tenant_id)
        if fila is None or fila["status"] != "confirmed" or fila["google_calendar_event_id"]:
            return
        try:
            servicio = fabrica(conn, cliente_http)
        except ErrorConfiguracionCalendar:
            logger.warning("calendario_no_configurado")
            return
        resultado = await servicio.sincronizar_booking(
            BookingCalendario(
                id=fila["id"],
                status=fila["status"],
                professional_id=fila["professional_id"],
                servicio=fila["servicio"],
                cliente_nombre=fila["cliente"] or "",
                cliente_telefono=fila["telefono"] or "",
                inicio=fila["inicio"],
                fin=fila["fin"],
            )
        )
        if resultado.accion is AccionSync.CREADO and resultado.event_id:
            await conn.execute(_GUARDAR_EVENTO, booking_id, tenant_id, resultado.event_id)


def sincronizar_reserva(tenant_id: uuid.UUID, booking_id: uuid.UUID) -> AccionPosterior:
    """Acción para `ctx.despues_del_commit`: crea el evento del turno confirmado y guarda su id."""

    async def accion(recursos: Recursos) -> None:
        propio = recursos.cliente_http is None
        cliente = httpx.AsyncClient(timeout=15.0) if propio else recursos.cliente_http
        try:
            await _sincronizar(recursos, tenant_id, booking_id, cliente)
        except ErrorCalendar as error:
            # Solo el tipo de error: ni el mensaje ni el booking llevan datos del cliente a los logs.
            logger.warning("calendar_sync_fallido", extra={"error": type(error).__name__})
        finally:
            if propio:
                await cliente.aclose()

    return accion
