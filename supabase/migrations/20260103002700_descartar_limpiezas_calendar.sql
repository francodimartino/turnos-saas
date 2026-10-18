-- Descarte manual de limpiezas de Google Calendar y orden de la lista de pendientes (Ola 4, tarea 14).
--
-- Problema: una limpieza que nunca se puede resolver (el evento ya no existe, la cuenta de Google se
-- desconectó para siempre) quedaba pendiente eternamente y el job la reintentaba sin fin; y las limpiezas sin
-- integración resoluble (integration_id nulo) ocupaban el límite de list_calendar_scrubs_pending y
-- desplazaban a las nuevas, que sí se pueden reintentar.
--
-- Cambios (no destructivos: solo create or replace de funciones, una acción nueva en audit_log y un índice):
--   * calendar_scrub_discarded  nueva acción de audit_log: {event_id, integration_id?, motivo}. Es terminal
--     igual que calendar_scrub_resolved: la limpieza deja de listarse. Si después aparece un nuevo
--     calendar_scrub_failed del mismo evento, vuelve a ser pendiente (el descarte es histórico).
--   * discard_calendar_scrub(p_tenant_id, p_event_id, p_motivo)  SECURITY DEFINER, solo platform_admin.
--     Motivo obligatorio (recortado a 200 caracteres; no debe llevar PII). P0002 si el evento no está
--     pendiente para ese tenant; invalid_parameter_value si faltan datos.
--   * list_calendar_scrubs_pending  ahora devuelve primero las que tienen integración (la más vieja primero)
--     y después las de integration_id nulo (la más vieja primero): estas últimas ya no desplazan a las nuevas
--     del límite.
--   * El visor del panel admin lee audit_log directo y distingue pendiente / reintento fallido / resuelta /
--     descartada por la acción.
--
-- El índice parcial viejo (audit_log_calendar_scrub_idx, 3 acciones) ya no sirve a las consultas nuevas
-- (su predicado no implica el de 4 acciones). Se crea el nuevo y se deja el viejo: quitarlo es un DROP que
-- queda para una migración aparte con su gate.

create index if not exists audit_log_calendar_scrub4_idx
  on public.audit_log (tenant_id, (metadata ->> 'event_id'))
  where action in ('calendar_scrub_failed', 'calendar_scrub_retry_failed', 'calendar_scrub_resolved',
                   'calendar_scrub_discarded');

create or replace function public.calendar_scrubs_pending_core(p_tenant_id uuid default null, p_event_id text default null)
returns table (tenant_id uuid, integration_id uuid, event_id text, attempts integer,
               first_failed_at timestamptz, last_failed_at timestamptz, last_motivo text)
language sql
stable
set search_path = ''
as $$
  with filas as (
    select a.tenant_id, a.metadata ->> 'event_id' as event_id, a.action, a.chain_seq, a.created_at,
           a.metadata ->> 'motivo' as motivo,
           case when a.metadata ->> 'integration_id' ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                then (a.metadata ->> 'integration_id')::uuid end as integration_id
      from public.audit_log a
     where a.action in ('calendar_scrub_failed', 'calendar_scrub_retry_failed', 'calendar_scrub_resolved',
                        'calendar_scrub_discarded')
       and a.tenant_id is not null
       and a.metadata ->> 'event_id' is not null
       and (p_tenant_id is null or a.tenant_id = p_tenant_id)
       and (p_event_id is null or a.metadata ->> 'event_id' = p_event_id)
  ), ultimo_ok as (
    select f.tenant_id, f.event_id, max(f.chain_seq) as seq
      from filas f where f.action in ('calendar_scrub_resolved', 'calendar_scrub_discarded')
     group by f.tenant_id, f.event_id
  ), pendientes as (
    select f.*
      from filas f
      left join ultimo_ok u on u.tenant_id = f.tenant_id and u.event_id = f.event_id
     where f.action in ('calendar_scrub_failed', 'calendar_scrub_retry_failed')
       and f.chain_seq > coalesce(u.seq, 0)
  ), agrupadas as (
    select p.tenant_id, p.event_id,
           count(*)::integer as attempts,
           min(p.created_at) as first_failed_at,
           max(p.created_at) as last_failed_at,
           (array_agg(p.motivo order by p.chain_seq desc))[1] as last_motivo,
           (array_agg(p.integration_id order by p.chain_seq desc) filter (where p.integration_id is not null))[1]
             as integration_id
      from pendientes p
     group by p.tenant_id, p.event_id
  )
  select g.tenant_id,
         coalesce(g.integration_id,
                  (select ti.id
                     from public.bookings b
                     join public.tenant_integrations ti
                       on ti.tenant_id = b.tenant_id and ti.integration_type = 'google_calendar'
                      and ti.professional_id = b.professional_id
                    where b.tenant_id = g.tenant_id and b.google_calendar_event_id = g.event_id
                    order by b.id
                    limit 1)),
         g.event_id, g.attempts, g.first_failed_at, g.last_failed_at, left(g.last_motivo, 200)
    from agrupadas g
   order by g.first_failed_at, g.tenant_id, g.event_id
$$;

revoke all on function public.calendar_scrubs_pending_core(uuid, text)
  from public, anon, authenticated, service_role;

create or replace function public.list_calendar_scrubs_pending(p_limit integer default 200)
returns table (tenant_id uuid, integration_id uuid, event_id text, attempts integer,
               first_failed_at timestamptz, last_failed_at timestamptz, last_motivo text)
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
  if p_limit is null or p_limit < 1 or p_limit > 1000 then
    raise exception 'limite debe estar entre 1 y 1000' using errcode = 'invalid_parameter_value';
  end if;

  -- Primero las que tienen integración (reintentables), la más vieja primero; después las de integración
  -- nula, que no se pueden reintentar y no deben ocupar el límite en lugar de las nuevas.
  return query
    select c.tenant_id, c.integration_id, c.event_id, c.attempts, c.first_failed_at, c.last_failed_at, c.last_motivo
      from public.calendar_scrubs_pending_core() c
     order by (c.integration_id is null), c.first_failed_at, c.tenant_id, c.event_id
     limit p_limit;
end
$$;

revoke all on function public.list_calendar_scrubs_pending(integer) from public, anon, authenticated, service_role;
grant execute on function public.list_calendar_scrubs_pending(integer) to purge_job;

create or replace function public.discard_calendar_scrub(p_tenant_id uuid, p_event_id text, p_motivo text)
returns void
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_fila record;
  v_motivo text := left(nullif(btrim(coalesce(p_motivo, '')), ''), 200);
begin
  if p_tenant_id is null or p_event_id is null or btrim(p_event_id) = '' or v_motivo is null then
    raise exception 'tenant_id, event_id y motivo son obligatorios' using errcode = 'invalid_parameter_value';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('calendar_scrub:' || p_tenant_id::text || ':' || p_event_id, 0));

  select * into v_fila from public.calendar_scrubs_pending_core(p_tenant_id, p_event_id);
  if not found then
    raise exception 'limpieza inexistente o ya resuelta' using errcode = 'no_data_found';
  end if;

  perform public.audit_log_append(p_tenant_id, 'platform_admin', null, 'calendar_scrub_discarded', 'tenants',
    p_tenant_id,
    jsonb_strip_nulls(jsonb_build_object('event_id', p_event_id, 'integration_id', v_fila.integration_id,
                                         'motivo', v_motivo)));
end
$$;

revoke all on function public.discard_calendar_scrub(uuid, text, text) from public, anon, authenticated, service_role;
grant execute on function public.discard_calendar_scrub(uuid, text, text) to platform_admin;
