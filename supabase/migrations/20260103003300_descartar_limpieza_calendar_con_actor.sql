-- discard_calendar_scrub registra en audit_log al admin que descarta: toma el actor de app.current_actor_id,
-- que platform_tenancy fija desde la identidad ya verificada. Antes dejaba actor_id en null.
-- Misma firma, mismo SECURITY DEFINER, mismo search_path y mismos permisos; solo cambia el actor.
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
  v_actor_id uuid := nullif(current_setting('app.current_actor_id', true), '')::uuid;
begin
  if p_tenant_id is null or p_event_id is null or btrim(p_event_id) = '' or v_motivo is null then
    raise exception 'tenant_id, event_id y motivo son obligatorios' using errcode = 'invalid_parameter_value';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('calendar_scrub:' || p_tenant_id::text || ':' || p_event_id, 0));

  select * into v_fila from public.calendar_scrubs_pending_core(p_tenant_id, p_event_id);
  if not found then
    raise exception 'limpieza inexistente o ya resuelta' using errcode = 'no_data_found';
  end if;

  perform public.audit_log_append(p_tenant_id, 'platform_admin', v_actor_id, 'calendar_scrub_discarded', 'tenants',
    p_tenant_id,
    jsonb_strip_nulls(jsonb_build_object('event_id', p_event_id, 'integration_id', v_fila.integration_id,
                                         'motivo', v_motivo)));
end
$$;

revoke all on function public.discard_calendar_scrub(uuid, text, text) from public, anon, authenticated, service_role;
grant execute on function public.discard_calendar_scrub(uuid, text, text) to platform_admin;
