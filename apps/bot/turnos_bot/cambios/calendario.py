"""Evento de Google Calendar de un turno cancelado o movido por el cliente (ARCHITECTURE.md §7.1).

Corre DESPUÉS del commit (`ctx.despues_del_commit`): una API externa no puede ser parte de la transacción ni hacer
fallar el cambio. Vuelve a leer el turno y aplica su estado: `cancelled` borra el evento (si lo había), `confirmed`
lo reescribe en el horario nuevo (o lo crea si el turno todavía no tenía); un `pending` no toca nada, porque un
turno sin aprobar nunca va al calendario, y un cambio que quedó pendiente de aprobación tampoco: el evento sigue
en el horario original. Si Calendar no está configurado, el profesional no conectó el suyo o Google falla, el cambio
queda igual y solo se deja un log sin datos del cliente.
"""

import logging
import uuid

from platform_calendar import AccionSync, ErrorConfiguracionCalendar
from platform_tenancy import tenant_tx
from turnos_negocio.calendario import evento_de_turno

from turnos_bot.compartido import accion_de_calendario
from turnos_bot.despachador import AccionPosterior, Recursos
from turnos_bot.identidad.calendario import fabrica_calendario_deshabilitada

logger = logging.getLogger("bot.cambios.calendario")

SQL_LEER_TURNO = """
select b.id, b.status, b.professional_id, b.google_calendar_event_id,
       lower(b.time_range) as inicio, upper(b.time_range) as fin,
       s.name as servicio, ec.name as cliente, (ec.deleted_at is not null) as anonimizado
from public.bookings b
join public.services s on s.tenant_id = b.tenant_id and s.id = b.service_id
join public.end_customers ec on ec.tenant_id = b.tenant_id and ec.id = b.end_customer_id
where b.id = $1 and b.tenant_id = $2
"""

# El id solo se guarda si cambió: otra sincronización concurrente que ya lo dejó igual no se pisa.
SQL_GUARDAR_EVENTO = (
    "update public.bookings set google_calendar_event_id = $3::text "
    "where id = $1 and tenant_id = $2 and google_calendar_event_id is distinct from $3::text"
)


async def _sincronizar(recursos: Recursos, tenant_id: uuid.UUID, booking_id: uuid.UUID, cliente_http) -> None:
    fabrica = recursos.fabrica_calendario or fabrica_calendario_deshabilitada
    # El almacén de secretos usa la conexión: la transacción con el contexto de tenant sigue abierta.
    async with tenant_tx(recursos.pool, tenant_id) as conn:
        fila = await conn.fetchrow(SQL_LEER_TURNO, booking_id, tenant_id)
        if fila is None:
            return
        evento_actual = fila["google_calendar_event_id"]
        estado = fila["status"]
        if estado == "cancelled" and not evento_actual:
            return
        if estado not in ("cancelled", "confirmed"):
            return
        try:
            servicio = fabrica(conn, cliente_http)
        except ErrorConfiguracionCalendar:
            logger.warning("calendario_no_configurado")
            return
        if estado == "cancelled":
            resultado = await servicio.retirar_evento(fila["professional_id"], evento_actual)
        else:
            evento = evento_de_turno(
                servicio=fila["servicio"],
                cliente_nombre=fila["cliente"],
                cliente_anonimizado=fila["anonimizado"],
                inicio=fila["inicio"],
                fin=fila["fin"],
                id_externo=evento_actual,
            )
            resultado = await servicio.sincronizar_evento(fila["professional_id"], evento)
        if resultado.accion is not AccionSync.SIN_CONEXION and resultado.event_id != evento_actual:
            await conn.execute(SQL_GUARDAR_EVENTO, booking_id, tenant_id, resultado.event_id)


def sincronizar_cambio(tenant_id: uuid.UUID, booking_id: uuid.UUID) -> AccionPosterior:
    """Acción para `ctx.despues_del_commit`: deja el evento del turno como quedó (borrado, movido o creado)."""

    async def sincronizar(recursos: Recursos, cliente_http) -> None:
        await _sincronizar(recursos, tenant_id, booking_id, cliente_http)

    return accion_de_calendario(sincronizar, logger)
