-- Seguimiento y reintento de las limpiezas de Google Calendar que fallaron (calendar_scrub_failed).
--
-- Problema: cuando la anonimización o la purga no logran limpiar un evento de Calendar, solo se registra
-- calendar_scrub_failed en audit_log y nadie lo vuelve a intentar: el nombre del cliente puede quedar para
-- siempre en un calendario personal. Esta migración agrega las dos funciones de base que necesita un job
-- diario (tarea siguiente) para listar lo pendiente y registrar el resultado de cada reintento.
-- No destructiva: solo agrega funciones y un índice parcial; no modifica ninguna fila existente.
--
-- Modelo: audit_log es append-only, así que el estado de cada limpieza se deduce de tres acciones, por evento
-- (tenant_id + metadata->>'event_id'):
--   calendar_scrub_failed        primer fallo (lo escriben el bot, el dashboard y la purga; ya existía)
--   calendar_scrub_retry_failed  un reintento que volvió a fallar   {event_id, integration_id?, motivo, attempt}
--   calendar_scrub_resolved      el evento se limpió                 {event_id, integration_id?}
-- Una limpieza está PENDIENTE si tiene algún fallo (failed o retry_failed) posterior a su último resolved (o
-- ninguno). Una resuelta no vuelve a listarse. El orden se toma de chain_seq (la cadena de hashes del
-- audit_log la arma el trigger; acá solo se inserta con audit_log_append).
--
-- INTERFAZ PARA EL BACKEND (tarea 12: job de purga diario, rol purge_job; solo él ejecuta estas funciones:
--   el EXECUTE exclusivo es el control de acceso, como en list_tenants_due_for_purge / begin_tenant_purge)
--   1. select * from public.list_calendar_scrubs_pending(:limite default 200)
--        -> (tenant_id uuid, integration_id uuid, event_id text, attempts integer, first_failed_at timestamptz,
--            last_failed_at timestamptz, last_motivo text)
--        Una fila por evento pendiente, de todos los tenants, la más vieja primero. attempts = fallos
--        registrados (el primer fallo cuenta como el intento 1, así que un reintento fallido deja attempts = 2).
--        integration_id sale del fallo; si no lo traía (bot/dashboard solo guardan event_id y motivo) se
--        resuelve con la integración google_calendar del profesional del turno que tiene ese evento; puede
--        quedar null (sin integración o credenciales ya borradas por la purga): el job lo reporta, no lo limpia.
--        Sin PII: ids, contadores, fechas y el motivo (texto corto de código, recortado a 200 caracteres).
--        limite entre 1 y 1000 (otro valor: invalid_parameter_value).
--   2. El job obtiene las credenciales (get_integration_secret con el contexto del tenant), limpia el evento
--      en Calendar y registra el resultado, uno por llamada:
--      select public.record_calendar_scrub_result(:tenant_id, :event_id, :resuelta, :motivo)
--        resuelta = true  -> escribe calendar_scrub_resolved; el evento deja de listarse.
--        resuelta = false -> escribe calendar_scrub_retry_failed con motivo (obligatorio, recortado a 200
--                            caracteres, sin PII) y attempt; sigue listado con attempts + 1.
--        Devuelve void. P0002 (no_data_found) si el evento no está pendiente para ese tenant (inexistente o
--        ya resuelto: el job lo ignora); invalid_parameter_value si faltan datos.
--      Serializa por (tenant, evento) con un lock advisory: dos jobs a la vez no resuelven dos veces.
--   Alertas por umbral (N intentos o X días): las calcula el job con attempts y first_failed_at.
--   El visor del panel admin lee audit_log directo (las tres acciones) y distingue pendiente / reintento
--   fallido / resuelta por la acción.

-- Índice parcial para recorrer solo las filas de limpiezas por evento (audit_log es la tabla más grande).
create index if not exists audit_log_calendar_scrub_idx
  on public.audit_log (tenant_id, (metadata ->> 'event_id'))
  where action in ('calendar_scrub_failed', 'calendar_scrub_retry_failed', 'calendar_scrub_resolved');

-- Núcleo interno (sin EXECUTE de aplicación; solo lo llaman las funciones definer de abajo).
-- p_tenant_id / p_event_id opcionales: filtran a un evento.
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
     where a.action in ('calendar_scrub_failed', 'calendar_scrub_retry_failed', 'calendar_scrub_resolved')
       and a.tenant_id is not null
       and a.metadata ->> 'event_id' is not null
       and (p_tenant_id is null or a.tenant_id = p_tenant_id)
       and (p_event_id is null or a.metadata ->> 'event_id' = p_event_id)
  ), ultimo_ok as (
    select f.tenant_id, f.event_id, max(f.chain_seq) as seq
      from filas f where f.action = 'calendar_scrub_resolved'
     group by f.tenant_id, f.event_id
  ), pendientes as (
    select f.*
      from filas f
      left join ultimo_ok u on u.tenant_id = f.tenant_id and u.event_id = f.event_id
     where f.action <> 'calendar_scrub_resolved'
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

  return query
    select c.tenant_id, c.integration_id, c.event_id, c.attempts, c.first_failed_at, c.last_failed_at, c.last_motivo
      from public.calendar_scrubs_pending_core() c
     limit p_limit;
end
$$;

revoke all on function public.list_calendar_scrubs_pending(integer) from public, anon, authenticated, service_role;
grant execute on function public.list_calendar_scrubs_pending(integer) to purge_job;

create or replace function public.record_calendar_scrub_result(
  p_tenant_id uuid, p_event_id text, p_resuelta boolean, p_motivo text default null)
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
  if p_tenant_id is null or p_event_id is null or btrim(p_event_id) = '' or p_resuelta is null then
    raise exception 'tenant_id, event_id y resuelta son obligatorios' using errcode = 'invalid_parameter_value';
  end if;
  if not p_resuelta and v_motivo is null then
    raise exception 'un reintento fallido requiere motivo' using errcode = 'invalid_parameter_value';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('calendar_scrub:' || p_tenant_id::text || ':' || p_event_id, 0));

  select * into v_fila from public.calendar_scrubs_pending_core(p_tenant_id, p_event_id);
  if not found then
    raise exception 'limpieza inexistente o ya resuelta' using errcode = 'no_data_found';
  end if;

  if p_resuelta then
    perform public.audit_log_append(p_tenant_id, 'system', null, 'calendar_scrub_resolved', 'tenants', p_tenant_id,
      jsonb_strip_nulls(jsonb_build_object('event_id', p_event_id, 'integration_id', v_fila.integration_id)));
  else
    perform public.audit_log_append(p_tenant_id, 'system', null, 'calendar_scrub_retry_failed', 'tenants',
      p_tenant_id,
      jsonb_strip_nulls(jsonb_build_object('event_id', p_event_id, 'integration_id', v_fila.integration_id,
                                           'motivo', v_motivo, 'attempt', v_fila.attempts + 1)));
  end if;
end
$$;

revoke all on function public.record_calendar_scrub_result(uuid, text, boolean, text)
  from public, anon, authenticated, service_role;
grant execute on function public.record_calendar_scrub_result(uuid, text, boolean, text) to purge_job;
