-- bookings: cambio de horario pendiente de aprobación (§2.12, §7.1).
--
-- Un turno 'confirmed' de un servicio en modo 'approval' puede tener UN cambio de horario pendiente: el cliente
-- pidió moverlo y el negocio todavía no resolvió. No es un estado nuevo: son tres columnas nulas en bookings.
--   starts_at_propuesto, ends_at_propuesto  horario pedido (el original sigue siendo time_range, sin tocar)
--   cambio_pendiente_expira_at              hasta cuándo el negocio puede resolverlo
-- Check: o están las tres o ninguna, solo en 'confirmed', y el fin es posterior al inicio.
--
-- Qué ocupa la agenda mientras hay un cambio pendiente:
--   * El horario ORIGINAL sigue ocupado: sigue en time_range, que cubre la restricción de exclusión
--     bookings_sin_doble_booking (no se reemplaza ni se debilita).
--   * El horario PROPUESTO también: una restricción de exclusión no puede comparar una columna de una fila contra
--     otra columna de otra, así que lo hace el trigger bookings_validar_cambio_pendiente, bajo el lock advisory
--     por (tenant, profesional) con la misma clave que usan el bot, el dashboard y la liberación de vencidos
--     ('turnos_profesional:<tenant>:<profesional>'). Rechaza (23P01, como la restricción) tanto proponer un
--     horario que choca con otro turno vigente o con otra propuesta, como crear o mover un turno encima de una
--     propuesta pendiente de otro. El propio turno no cuenta contra sí mismo (se puede proponer 10:15 sobre 10:00).
--
-- Cómo se resuelve (todo con un UPDATE que deja las tres columnas en null):
--   * aprobado: time_range pasa a ser exactamente [starts_at_propuesto, ends_at_propuesto).
--   * rechazado o vencido: time_range queda como estaba.
--   * mientras el cambio sigue pendiente time_range no puede cambiar, ni la propuesta modificarse en el lugar.
--   * si el turno sale de 'confirmed' (se cancela o se completa) la propuesta se descarta y deja de bloquear.
--
-- Cambio vencido y todavía no revertido (cambio_pendiente_expira_at <= now()): no se puede aprobar (solo
-- rechazar, que es dejarlo con el horario original) y deja de bloquear la agenda: no cuenta como horario ocupado,
-- igual que un pending vencido se libera al reservar. Quien lo ocupe gana; el job lo revierte después.
--
-- Transiciones de estado: no cambian (confirmed->confirmed ya era válida); solo se limpia la propuesta al salir
-- de 'confirmed'.
--
-- revert_expired_booking_changes(): revierte los cambios vencidos (cambio_pendiente_expira_at <= now()) al horario
-- original y, EN LA MISMA SENTENCIA, encola el aviso en notification_outbox (plantilla 'cambio_horario_vencido',
-- params {booking_id, inicio, inicio_propuesto}, primer intento a los 5 minutos), igual que expire_pending_bookings:
-- si el proceso muere después del commit, la fila 'pending' del outbox la envía el reescaneo. Devuelve las mismas
-- columnas que expire_pending_bookings (sin datos personales cuando no hay a quién avisar) más starts_at_propuesto
-- y outbox_id (null si no hay nada que avisar). Solo EXECUTE para scheduler_job.
-- Migración no destructiva: columnas nulas nuevas, un trigger y una función.

alter table public.bookings
  add column starts_at_propuesto timestamptz null,
  add column ends_at_propuesto timestamptz null,
  add column cambio_pendiente_expira_at timestamptz null;

alter table public.bookings
  add constraint bookings_cambio_pendiente_completo
    check ((starts_at_propuesto is null) = (ends_at_propuesto is null)
           and (starts_at_propuesto is null) = (cambio_pendiente_expira_at is null)),
  add constraint bookings_cambio_pendiente_solo_confirmado
    check (starts_at_propuesto is null or status = 'confirmed'),
  add constraint bookings_cambio_pendiente_rango
    check (starts_at_propuesto is null or ends_at_propuesto > starts_at_propuesto);

-- Al salir de 'confirmed' la propuesta se descarta (si no, el check de arriba rechazaría la cancelación).
create or replace function public.bookings_validar_transicion_estado()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.status = old.status then
    return new;
  end if;

  if not ((old.status = 'pending'   and new.status in ('confirmed', 'cancelled'))
       or (old.status = 'confirmed' and new.status in ('completed', 'cancelled'))) then
    raise exception 'transición de estado inválida en bookings: % -> %', old.status, new.status
      using errcode = '23514';
  end if;

  -- sale de 'pending': ya no hay vencimiento (new.status nunca es 'pending' en este punto)
  new.pending_expires_at := null;
  -- sale de 'confirmed': un cambio de horario pendiente ya no tiene sentido y deja de bloquear la agenda
  new.starts_at_propuesto := null;
  new.ends_at_propuesto := null;
  new.cambio_pendiente_expira_at := null;
  return new;
end
$$;

create function public.bookings_validar_cambio_pendiente()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_propuesto tstzrange;
  v_revisar_original boolean;
  v_revisar_propuesto boolean;
begin
  -- propuesta incompleta o invertida: la rechazan los checks de la tabla (este trigger corre antes y no puede
  -- armar un rango con ella)
  if (new.starts_at_propuesto is null) <> (new.ends_at_propuesto is null)
     or (new.starts_at_propuesto is null) <> (new.cambio_pendiente_expira_at is null)
     or new.ends_at_propuesto <= new.starts_at_propuesto then
    return new;
  end if;

  if tg_op = 'INSERT' then
    if new.starts_at_propuesto is not null then
      raise exception 'un turno nuevo no puede nacer con un cambio de horario pendiente' using errcode = '23514';
    end if;
    v_revisar_original := new.status <> 'cancelled';
    v_revisar_propuesto := false;
  else
    -- el horario original no cambia mientras el cambio sigue pendiente
    if old.starts_at_propuesto is not null and new.starts_at_propuesto is not null then
      if new.time_range is distinct from old.time_range then
        raise exception 'el horario original no puede cambiar mientras hay un cambio pendiente'
          using errcode = '23514';
      end if;
      if new.starts_at_propuesto is distinct from old.starts_at_propuesto
         or new.ends_at_propuesto is distinct from old.ends_at_propuesto then
        raise exception 'el cambio pendiente no se modifica: se resuelve primero' using errcode = '23514';
      end if;
    end if;

    -- resolución: aprobado (time_range = propuesto) o rechazado/vencido (time_range = original)
    if old.starts_at_propuesto is not null and new.starts_at_propuesto is null
       and new.time_range is distinct from old.time_range
       and new.time_range is distinct from tstzrange(old.starts_at_propuesto, old.ends_at_propuesto) then
      raise exception 'al resolver un cambio pendiente el horario es el propuesto o el original'
        using errcode = '23514';
    end if;

    -- un cambio vencido no se aprueba: solo se rechaza (o lo revierte el job)
    if old.starts_at_propuesto is not null and new.starts_at_propuesto is null
       and new.time_range is distinct from old.time_range
       and old.cambio_pendiente_expira_at <= now() then
      raise exception 'el cambio pendiente venció: no se puede aprobar' using errcode = '23514';
    end if;

    -- solo hace falta mirar la agenda si hay algo nuevo que ocupa lugar
    v_revisar_original := new.status <> 'cancelled'
                          and (new.time_range is distinct from old.time_range
                               or new.professional_id is distinct from old.professional_id)
                          and not (old.starts_at_propuesto is not null
                                   and new.time_range = tstzrange(old.starts_at_propuesto, old.ends_at_propuesto)
                                   and new.professional_id = old.professional_id);
    v_revisar_propuesto := new.starts_at_propuesto is not null
                           and (old.starts_at_propuesto is null
                                or new.professional_id is distinct from old.professional_id);
  end if;

  if not (v_revisar_original or v_revisar_propuesto) then
    return new;
  end if;

  perform pg_advisory_xact_lock(
    hashtextextended('turnos_profesional:' || new.tenant_id::text || ':' || new.professional_id::text, 0));

  if v_revisar_original and exists (
    select 1 from public.bookings o
     where o.tenant_id = new.tenant_id
       and o.professional_id = new.professional_id
       and o.id <> new.id
       and o.status = 'confirmed'
       and o.starts_at_propuesto is not null
       and o.cambio_pendiente_expira_at > now()
       and tstzrange(o.starts_at_propuesto, o.ends_at_propuesto) && new.time_range
  ) then
    raise exception 'el horario está reservado por un cambio de horario pendiente' using errcode = '23P01';
  end if;

  if v_revisar_propuesto then
    v_propuesto := tstzrange(new.starts_at_propuesto, new.ends_at_propuesto);
    if exists (
      select 1 from public.bookings o
       where o.tenant_id = new.tenant_id
         and o.professional_id = new.professional_id
         and o.id <> new.id
         and o.status <> 'cancelled'
         and (o.time_range && v_propuesto
              or (o.starts_at_propuesto is not null
                  and o.cambio_pendiente_expira_at > now()
                  and tstzrange(o.starts_at_propuesto, o.ends_at_propuesto) && v_propuesto))
    ) then
      raise exception 'el horario propuesto choca con otro turno' using errcode = '23P01';
    end if;
  end if;

  return new;
end
$$;

-- BEFORE: ordena después de bookings_forzar_tenant_id y bookings_transiciones_estado (orden alfabético)
create trigger bookings_validar_cambio_pendiente
  before insert or update of time_range, professional_id, starts_at_propuesto, ends_at_propuesto,
                             cambio_pendiente_expira_at
  on public.bookings
  for each row execute function public.bookings_validar_cambio_pendiente();

create function public.revert_expired_booking_changes()
returns table (tenant_id uuid, booking_id uuid, channel text, channel_user_id text, notify boolean,
               end_customer_id uuid, starts_at timestamptz, service_name text, outbox_id uuid,
               starts_at_propuesto timestamptz)
language sql
volatile
security definer
set search_path = ''
as $$
  with vencidos as (
    update public.bookings b
       set starts_at_propuesto = null,
           ends_at_propuesto = null,
           cambio_pendiente_expira_at = null
      from (
        select x.id, x.starts_at_propuesto as propuesto
          from public.bookings x
         where x.status = 'confirmed'
           and x.cambio_pendiente_expira_at <= now()
           for update
      ) v
     where b.id = v.id
    returning b.tenant_id, b.id, b.end_customer_id, b.service_id, lower(b.time_range) as inicio, v.propuesto
  ),
  destinos as (
    select v.tenant_id as tenant_id,
           v.id as booking_id,
           v.end_customer_id as end_customer_id,
           v.inicio as inicio,
           v.propuesto as propuesto,
           s.name as service_name,
           ec.channel as channel,
           ec.channel_user_id as channel_user_id,
           n.notificar as notificar
      from vencidos v
      join public.tenants t on t.id = v.tenant_id
      join public.services s on s.tenant_id = v.tenant_id and s.id = v.service_id
      left join public.end_customers ec
        on ec.tenant_id = v.tenant_id and ec.id = v.end_customer_id
     cross join lateral (
       select (t.status = 'active'
               and ec.channel is not null
               and ec.channel_user_id is not null
               and ec.deleted_at is null) as notificar
     ) n
  ),
  encolados as (
    insert into public.notification_outbox
      (tenant_id, end_customer_id, channel, template, params, next_attempt_at)
    select d.tenant_id, d.end_customer_id, d.channel, 'cambio_horario_vencido',
           jsonb_build_object('booking_id', d.booking_id, 'inicio', d.inicio, 'inicio_propuesto', d.propuesto),
           now() + interval '300 seconds'
      from destinos d
     where d.notificar and d.channel in ('telegram', 'whatsapp')
    returning public.notification_outbox.id as outbox_id,
              (public.notification_outbox.params ->> 'booking_id')::uuid as booking_id
  )
  select d.tenant_id,
         d.booking_id,
         case when d.notificar then d.channel end,
         case when d.notificar then d.channel_user_id end,
         d.notificar,
         case when d.notificar then d.end_customer_id end,
         d.inicio,
         d.service_name,
         e.outbox_id,
         d.propuesto
    from destinos d
    left join encolados e on e.booking_id = d.booking_id
$$;

revoke all on function public.revert_expired_booking_changes() from public, anon, authenticated, service_role;
grant execute on function public.revert_expired_booking_changes() to scheduler_job;
