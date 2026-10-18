-- expire_pending_bookings() y release_expired_pending_overlaps(profesional, rango) devuelven, además, el
-- inicio del turno cancelado (starts_at, timestamptz) y el nombre del servicio (service_name), para que el
-- aviso de vencimiento diga QUÉ turno venció (día, hora y servicio) en vez de un texto genérico.
--
-- INTERFAZ PARA LOS LLAMADORES (tareas 9 y 10, backend):
--   Forma anterior (se conserva con el mismo nombre y orden):
--     (tenant_id uuid, booking_id uuid, channel text, channel_user_id text, notify boolean, end_customer_id uuid)
--   Columnas nuevas, AL FINAL:
--     starts_at    timestamptz  -- lower(time_range) del turno cancelado; el llamador lo muestra en la zona del negocio
--     service_name text         -- services.name del turno
--   Solo agregan información del turno, ningún dato personal del cliente. Se devuelven siempre (también con
--   notify = false, donde igual no se manda nada): no dependen de la regla de notify. service_name no es nulo
--   (la FK compuesta de bookings garantiza que el servicio existe en el mismo tenant).
--   Los llamadores que leen las seis columnas anteriores por nombre siguen funcionando sin cambios.
--
-- Cambiar el tipo de retorno obliga a drop + create. No se pierden datos (las funciones no almacenan nada) y
-- no hay objetos dependientes; la semántica es idéntica a la de 20260103000400 y 20260103002100. Los permisos
-- se re-otorgan igual que antes: expire_pending_bookings solo a scheduler_job, release_expired_pending_overlaps
-- solo a bot_service; EXECUTE revocado de PUBLIC, anon, authenticated y service_role. Siguen siendo SECURITY
-- DEFINER con search_path vacío (los nombres de la meta-prueba 44 no cambian).

drop function public.expire_pending_bookings();

create function public.expire_pending_bookings()
returns table (tenant_id uuid, booking_id uuid, channel text, channel_user_id text, notify boolean,
               end_customer_id uuid, starts_at timestamptz, service_name text)
language sql
volatile
security definer
set search_path = ''
as $$
  with vencidos as (
    update public.bookings b
       set status = 'cancelled'
     where b.status = 'pending'
       and b.pending_expires_at <= now()
    returning b.tenant_id, b.id, b.end_customer_id, b.service_id, lower(b.time_range) as inicio
  )
  select v.tenant_id,
         v.id,
         case when n.notificar then ec.channel end,
         case when n.notificar then ec.channel_user_id end,
         n.notificar,
         case when n.notificar then v.end_customer_id end,
         v.inicio,
         s.name
    from vencidos v
    join public.tenants t on t.id = v.tenant_id
    join public.services s on s.tenant_id = v.tenant_id and s.id = v.service_id
    left join public.end_customers ec
      on ec.tenant_id = v.tenant_id and ec.id = v.end_customer_id
   cross join lateral (
     select (t.status = 'active'
             and ec.channel is not null
             and ec.channel_user_id is not null) as notificar
   ) n
$$;

revoke all on function public.expire_pending_bookings() from public, anon, authenticated, service_role;
grant execute on function public.expire_pending_bookings() to scheduler_job;

drop function public.release_expired_pending_overlaps(uuid, tstzrange);

create function public.release_expired_pending_overlaps(p_professional_id uuid, p_range tstzrange)
returns table (tenant_id uuid, booking_id uuid, channel text, channel_user_id text, notify boolean,
               end_customer_id uuid, starts_at timestamptz, service_name text)
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
    returning b.tenant_id, b.id, b.end_customer_id, b.service_id, lower(b.time_range) as inicio
  )
  select l.tenant_id,
         l.id,
         case when n.notificar then ec.channel end,
         case when n.notificar then ec.channel_user_id end,
         n.notificar,
         case when n.notificar then l.end_customer_id end,
         l.inicio,
         s.name
    from liberados l
    join public.tenants t on t.id = l.tenant_id
    join public.services s on s.tenant_id = l.tenant_id and s.id = l.service_id
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
