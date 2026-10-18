-- release_expired_pending_overlaps(profesional, rango): libera los turnos 'pending' ya vencidos
-- (pending_expires_at <= now()) de un profesional que se solapan con un rango, para que el bot pueda reservar
-- ese horario sin chocar con el exclude bookings_sin_doble_booking (un pending vencido sigue ocupando el
-- horario hasta que el job de vencimiento lo cancela). Migración no destructiva: solo agrega una función.
--
-- Semántica:
--   * Cancela por la transición válida pending -> cancelled (el trigger de transiciones limpia
--     pending_expires_at y el trigger AFTER UPDATE devuelve la sesión del pack, una sola vez).
--   * No toca pendientes todavía vigentes, ni confirmed/completed/cancelled, ni turnos que no se solapan
--     (&& sobre time_range, igual que el exclude).
--   * Devuelve las filas liberadas con la MISMA forma que expire_pending_bookings(): (tenant_id, booking_id,
--     channel, channel_user_id, notify, end_customer_id), con la misma regla de notify (tenant 'active' y
--     cliente con canal vinculado; si no, canal/usuario/cliente en null).
--
-- Cómo se entera el cliente liberado (tarea 9, bot): el turno queda 'cancelled', así que el job
-- expire_pending_bookings() ya NO lo encuentra; el aviso de vencimiento lo manda quien llama a esta función,
-- con las filas que devuelve (notify = true -> mismo mensaje y registro en notifications_log por
-- record_notification_attempt que usa el job). Es exactamente una vez: lo libera esta función o lo vence el
-- job, nunca ambos (el UPDATE solo ve filas en 'pending' y, ante una carrera, la segunda espera el row lock y
-- no encuentra la fila en 'pending').
--
-- Aislamiento: el contexto de tenant sale del rol que llama (app.current_tenant_id, el mismo que fija
-- platform_tenancy); sin contexto se rechaza (42501). El profesional tiene que ser de ese tenant: uno
-- inexistente o ajeno da el mismo error. Nunca recibe un tenant como parámetro.
--
-- Concurrencia: toma el lock advisory por (tenant, profesional) con la MISMA clave que
-- apps/shared/turnos_queries/bloqueo.py ('turnos_profesional:<tenant>:<profesional>'), así se serializa con
-- las altas del dashboard y del bot. Es re-entrante en la misma sesión: si el bot ya lo tomó antes de llamar,
-- no espera. Se libera al cerrar la transacción.
--
-- Solo bot_service puede ejecutarla (EXECUTE revocado de PUBLIC y de los demás roles).

create function public.release_expired_pending_overlaps(p_professional_id uuid, p_range tstzrange)
returns table (tenant_id uuid, booking_id uuid, channel text, channel_user_id text, notify boolean,
               end_customer_id uuid)
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_ctx uuid := nullif(current_setting('app.current_tenant_id', true), '')::uuid;
begin
  if v_ctx is null then
    raise exception 'contexto de tenant requerido' using errcode = 'insufficient_privilege';
  end if;

  if not exists (
    select 1 from public.professionals p
     where p.id = p_professional_id and p.tenant_id = v_ctx
  ) then
    raise exception 'profesional inexistente o de otro tenant' using errcode = 'insufficient_privilege';
  end if;

  if p_range is null or isempty(p_range) then
    raise exception 'rango inválido' using errcode = 'invalid_parameter_value';
  end if;

  perform pg_advisory_xact_lock(
    hashtextextended('turnos_profesional:' || v_ctx::text || ':' || p_professional_id::text, 0));

  return query
  with liberados as (
    update public.bookings b
       set status = 'cancelled'
     where b.tenant_id = v_ctx
       and b.professional_id = p_professional_id
       and b.status = 'pending'
       and b.pending_expires_at <= now()
       and b.time_range && p_range
    returning b.tenant_id, b.id, b.end_customer_id
  )
  select l.tenant_id,
         l.id,
         case when n.notificar then ec.channel end,
         case when n.notificar then ec.channel_user_id end,
         n.notificar,
         case when n.notificar then l.end_customer_id end
    from liberados l
    join public.tenants t on t.id = l.tenant_id
    left join public.end_customers ec
      on ec.tenant_id = l.tenant_id and ec.id = l.end_customer_id
   cross join lateral (
     select (t.status = 'active'
             and ec.channel is not null
             and ec.channel_user_id is not null) as notificar
   ) n;
end
$$;

revoke all on function public.release_expired_pending_overlaps(uuid, tstzrange)
  from public, anon, authenticated, service_role;
grant execute on function public.release_expired_pending_overlaps(uuid, tstzrange) to bot_service;
