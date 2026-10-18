"""Lógica y SQL de la gestión de turnos (`bookings`) y packs manuales desde el dashboard (ARCHITECTURE.md §7.1).

Todas las sentencias filtran `tenant_id = $1` de forma explícita (la RLS es la segunda barrera, no la única) y el
tenant sale siempre de la sesión verificada. `dashboard_service` no puede hacer DELETE en `bookings` ni
`booking_packs`: los turnos se cancelan. Las transiciones válidas de estado, el exclusion constraint de doble
reserva y la validación de packs (`origin='manual_grant'` o `payment_status='paid'`) las fuerza la base; acá solo se
adelanta el chequeo de estado para dar un 409 claro, y se deja la base como respaldo.
"""

from datetime import datetime, timedelta, timezone  # noqa: UP017 (el entorno local puede ser 3.10)
from typing import Any

from platform_calendar import BookingCalendario
from turnos_queries.bloqueo import SQL_BLOQUEAR_PROFESIONAL

ESTADOS = ("pending", "confirmed", "cancelled", "completed")

# Argentina no tiene horario de verano: un desfase fijo evita depender de la base de zonas horarias del sistema.
_ZONA_AR = timezone(timedelta(hours=-3))

_COLUMNAS_PACK = (
    "id, tenant_id, end_customer_id, service_id, total_sessions, used_sessions, origin, payment_status, "
    "expires_at, created_at"
)

# El turno con todo lo que hace falta para operar sobre él. `for update of b` serializa dos cambios simultáneos
# del mismo turno (aprobar y cancelar a la vez). `professional_auth_user_id` es la cuenta vinculada al profesional.
SQL_TURNO = """
select b.id, b.tenant_id, b.end_customer_id, b.service_id, b.professional_id,
       lower(b.time_range) as start_at, upper(b.time_range) as end_at,
       b.status, b.deposit_status, b.booking_pack_id, b.pending_expires_at,
       coalesce(b.pending_expires_at <= now(), false) as vencido,
       b.google_calendar_event_id,
       ec.name as customer_name, ec.phone as customer_phone, ec.channel as customer_channel,
       ec.channel_user_id as customer_channel_user_id, ec.deleted_at as customer_deleted_at,
       s.name as service_name, p.name as professional_name,
       tu.auth_user_id as professional_auth_user_id
from public.bookings b
join public.end_customers ec on ec.id = b.end_customer_id and ec.tenant_id = $1
join public.services s on s.id = b.service_id and s.tenant_id = $1
join public.professionals p on p.id = b.professional_id and p.tenant_id = $1
left join public.tenant_users tu on tu.id = p.tenant_user_id and tu.tenant_id = $1
where b.tenant_id = $1 and b.id = $2
for update of b
"""

# El trigger de la base valida la transición y limpia `pending_expires_at` al salir de 'pending'.
SQL_CAMBIAR_ESTADO = "update public.bookings set status = $3 where tenant_id = $1 and id = $2"

__all__ = ["SQL_BLOQUEAR_PROFESIONAL"]  # re-export: lo usan las rutas como `t.SQL_BLOQUEAR_PROFESIONAL`

# Lock por (tenant, profesional) de altas y movimientos: la clave es compartida con el bot (ver ese módulo).

# Mover = actualizar el rango del mismo booking; un choque lo rechaza el exclusion constraint (23P01).
SQL_MOVER = (
    "update public.bookings set time_range = tstzrange($3::timestamptz, $4::timestamptz) "
    "where tenant_id = $1 and id = $2"
)

SQL_GUARDAR_EVENTO = "update public.bookings set google_calendar_event_id = $3 where tenant_id = $1 and id = $2"

# $2 servicio, $3 profesional, $4 cliente. Sin fila: alguno no existe en este tenant.
SQL_DATOS_ALTA = """
select s.duration_minutes, s.name as service_name, s.is_active as service_active,
       p.is_active as professional_active,
       exists (
         select 1 from public.professional_services ps
         where ps.tenant_id = $1 and ps.professional_id = p.id and ps.service_id = s.id
       ) as ofrece,
       ec.name as customer_name, ec.deleted_at as customer_deleted_at
from public.services s
join public.professionals p on p.tenant_id = $1 and p.id = $3
join public.end_customers ec on ec.tenant_id = $1 and ec.id = $4
where s.tenant_id = $1 and s.id = $2
"""

# Alta del negocio: nace confirmed (no pasa por aprobación), sin vencimiento.
SQL_INSERTAR_TURNO = """
insert into public.bookings
  (tenant_id, end_customer_id, service_id, professional_id, time_range, status, booking_pack_id)
values ($1, $2, $3, $4, tstzrange($5::timestamptz, $6::timestamptz), 'confirmed', $7)
returning id
"""

# Consume una sesión solo si el pack es de ese cliente y servicio, no venció y le quedan sesiones. Que sea
# `manual_grant` o esté pagado lo valida el trigger de `bookings` al insertar.
SQL_CONSUMIR_PACK = """
update public.booking_packs
set used_sessions = used_sessions + 1
where tenant_id = $1 and id = $2 and end_customer_id = $3 and service_id = $4
  and used_sessions < total_sessions and (expires_at is null or expires_at > now())
returning id
"""

SQL_CLIENTES_ELIMINADOS = """
select id, deleted_at from public.end_customers
where tenant_id = $1 and id = any($2::uuid[]) and deleted_at is not null
"""

SQL_CLIENTE_PACK = "select id, deleted_at from public.end_customers where tenant_id = $1 and id = $2"

SQL_INSERTAR_PACK = f"""
insert into public.booking_packs
  (tenant_id, end_customer_id, service_id, total_sessions, origin, payment_status, expires_at)
values ($1, $2, $3, $4, 'manual_grant', 'not_required', $5)
returning {_COLUMNAS_PACK}
"""

_PLANTILLAS = {
    "turno_confirmado": "Tu turno de {servicio} del {cuando} fue confirmado.",
    "turno_rechazado": "Tu solicitud de turno de {servicio} del {cuando} fue rechazada por el negocio.",
    "turno_cancelado": "Tu turno de {servicio} del {cuando} fue cancelado por el negocio.",
    "turno_movido": "Tu turno de {servicio} fue movido para el {cuando}.",
}

_OCULTOS = {
    "tenant_id",
    "vencido",
    "customer_channel",
    "customer_channel_user_id",
    "customer_deleted_at",
    "professional_auth_user_id",
}


def cuando(inicio: datetime) -> str:
    """Fecha y hora legibles en la zona del negocio (America/Argentina/Buenos_Aires)."""
    return inicio.astimezone(_ZONA_AR).strftime("%d/%m/%Y %H:%M")


def texto_aviso(plantilla: str, fila: Any, inicio: datetime) -> str:
    """Texto del aviso al cliente. Lleva servicio y horario, nunca el nombre ni el teléfono del cliente."""
    return _PLANTILLAS[plantilla].format(servicio=fila["service_name"], cuando=cuando(inicio))


def booking_para_calendario(fila: Any, estado: str, inicio: datetime, fin: datetime) -> BookingCalendario:
    """Lo que Calendar necesita. Un cliente anonimizado no tiene datos: el evento no los inventa."""
    anonimizado = fila["customer_deleted_at"] is not None
    return BookingCalendario(
        id=fila["id"],
        status=estado,
        professional_id=fila["professional_id"],
        servicio=fila["service_name"],
        cliente_nombre="(cliente anonimizado)" if anonimizado else (fila["customer_name"] or "Cliente"),
        inicio=inicio,
        fin=fin,
        google_calendar_event_id=fila["google_calendar_event_id"],
    )


def presentar_turno(fila: Any, anonimizado: bool) -> dict[str, Any]:
    """Fila de turno -> respuesta. `sin_cliente_identificable` viene de `end_customers.deleted_at` (§3.5)."""
    salida = {k: v for k, v in dict(fila).items() if k not in _OCULTOS}
    salida["sin_cliente_identificable"] = anonimizado
    return salida


def presentar_pack(fila: Any) -> dict[str, Any]:
    return {k: v for k, v in dict(fila).items() if k != "tenant_id"}
