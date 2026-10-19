"""Lectura de la base para el cálculo de huecos. Corre como `bot_service` dentro de `tenant_tx`.

La conexión debe venir de `platform_tenancy.tenant_tx`: la RLS limita todo al tenant del contexto.
"""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from turnos_negocio import ZONA_HORARIA

from turnos_dominio.huecos import UTC, ReglaSemanal, Ventana, huecos_libres

_SQL_SERVICIO = """
select s.duration_minutes
from public.services s
join public.professional_services ps on ps.service_id = s.id
join public.professionals p on p.id = ps.professional_id
where s.id = $1 and p.id = $2 and s.is_active and p.is_active
"""

_SQL_REGLAS = "select day_of_week, start_time, end_time from public.availability_rules where professional_id = $1"

# Mismo criterio que bookings_sin_doble_booking: todo status distinto de 'cancelled' ocupa el turno, salvo un
# 'pending' ya vencido (pending_expires_at <= now()): ese no se ofrece como ocupado porque reservar sobre él lo
# libera (release_expired_pending_overlaps, ver bot_app.reserva.repositorio). Los pending vigentes y los
# confirmed siguen ocupando. Los bloqueos con professional_id null
# son de todo el local, restringidos al tenant del profesional.
_SQL_OCUPADOS = """
select lower(time_range) as inicio, upper(time_range) as fin
from public.bookings
where professional_id = $1 and status <> 'cancelled'
  and not (status = 'pending' and pending_expires_at <= now())
  and time_range && tstzrange($2, $3)
union all
select lower(time_range), upper(time_range)
from public.availability_blocks
where (professional_id = $1
       or (professional_id is null and tenant_id = (select tenant_id from public.professionals where id = $1)))
  and time_range && tstzrange($2, $3)
"""

# Un rango sin cota (lower/upper null) ocupa hasta el infinito en esa dirección.
_MINIMO = datetime.min.replace(tzinfo=UTC)
_MAXIMO = datetime.max.replace(tzinfo=UTC)


async def calcular_huecos(
    conn: Any,
    profesional_id: Any,
    servicio_id: Any,
    rango: Ventana,
    *,
    paso_minutos: int = 15,
    zona: ZoneInfo = ZONA_HORARIA,
    no_antes_de: datetime | None = None,
) -> list[Ventana]:
    """Huecos libres del profesional para el servicio dentro de `rango`.

    Devuelve `[]` si el profesional o el servicio no existen o están inactivos, si el profesional no
    ofrece el servicio (`professional_services`) o si no es visible para el tenant del contexto.
    `no_antes_de` descarta los huecos que empiezan antes de ese instante (anticipación mínima de la reserva).
    """
    duracion = await conn.fetchval(_SQL_SERVICIO, servicio_id, profesional_id)
    if duracion is None:
        return []
    filas_reglas = await conn.fetch(_SQL_REGLAS, profesional_id)
    reglas = [ReglaSemanal(f["day_of_week"], f["start_time"], f["end_time"]) for f in filas_reglas]
    filas = await conn.fetch(_SQL_OCUPADOS, profesional_id, rango.inicio, rango.fin)
    ocupados = [Ventana(f["inicio"] or _MINIMO, f["fin"] or _MAXIMO) for f in filas]
    return huecos_libres(
        reglas, ocupados, duracion, rango, paso_minutos=paso_minutos, zona=zona, no_antes_de=no_antes_de
    )
