-- discard_calendar_scrub y el orden de list_calendar_scrubs_pending (Ola 4, tarea 14).
-- Se ejecutan como cada rol (`set role`), como en produccion.
begin;
select plan(21);

do $$
declare
  v_rol text;
begin
  foreach v_rol in array array['bot_service', 'dashboard_service', 'scheduler_job', 'platform_admin',
                               'platform_admin_readonly', 'purge_job'] loop
    execute format('grant %I to current_user with inherit false, set true', v_rol);
  end loop;
end
$$;

-- Ejecuta p_sql (devuelve un unico texto) como p_rol. Devuelve el texto o 'err <sqlstate>'.
create function pg_temp.como(p_rol text, p_sql text) returns text
language plpgsql as $$
declare
  v_res text;
begin
  execute format('set role %I', p_rol);
  begin
    execute p_sql into v_res;
  exception when others then
    reset role;
    return 'err ' || sqlstate;
  end;
  reset role;
  return v_res;
end
$$;

-- Eventos pendientes listados (orden de la funcion), separados por coma.
create function pg_temp.orden(p_limite integer default 200) returns text
language sql as $$
  select pg_temp.como('purge_job', format($q$
    select coalesce(string_agg(r.event_id, ',' order by r.n), '')
      from (select r.event_id, row_number() over () as n from public.list_calendar_scrubs_pending(%s) r) r
  $q$, p_limite))
$$;

insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000000a1', 'A', 'desc-a'),
  ('00000000-0000-0000-0000-0000000000a2', 'B', 'desc-b');

-- Mas filas sin integracion (s1..s4, las mas viejas) que el limite, y dos con integracion (c1 mas vieja que c2).
insert into public.audit_log (tenant_id, actor_type, action, entity, metadata, created_at) values
  ('00000000-0000-0000-0000-0000000000a1', 'system', 'calendar_scrub_failed', 'tenants',
   '{"event_id": "s1", "motivo": "sin_integracion"}', now() - interval '10 days'),
  ('00000000-0000-0000-0000-0000000000a1', 'system', 'calendar_scrub_failed', 'tenants',
   '{"event_id": "s2", "motivo": "sin_integracion"}', now() - interval '9 days'),
  ('00000000-0000-0000-0000-0000000000a1', 'system', 'calendar_scrub_failed', 'tenants',
   '{"event_id": "s3", "motivo": "sin_integracion"}', now() - interval '8 days'),
  ('00000000-0000-0000-0000-0000000000a2', 'system', 'calendar_scrub_failed', 'tenants',
   '{"event_id": "s4", "motivo": "sin_integracion"}', now() - interval '7 days'),
  ('00000000-0000-0000-0000-0000000000a1', 'system', 'calendar_scrub_failed', 'tenants',
   '{"event_id": "c2", "integration_id": "00000000-0000-0000-0000-0000000000f1", "motivo": "http_500"}',
   now() - interval '2 days'),
  ('00000000-0000-0000-0000-0000000000a1', 'system', 'calendar_scrub_failed', 'tenants',
   '{"event_id": "c1", "integration_id": "00000000-0000-0000-0000-0000000000f1", "motivo": "http_500"}',
   now() - interval '3 days');

-- Orden: con integracion primero (la mas vieja primero), despues las de integracion nula.
select is(pg_temp.orden(), 'c1,c2,s1,s2,s3,s4', 'primero las con integracion (mas vieja primero), despues las nulas');
-- Con mas filas sin integracion que el limite, las con integracion no quedan afuera.
select is(pg_temp.orden(2), 'c1,c2', 'limite 2 con 4 filas sin integracion mas viejas: salen las 2 con integracion');
select is(pg_temp.orden(3), 'c1,c2,s1', 'limite 3: las nulas solo completan el cupo');

-- Permisos: solo platform_admin ejecuta discard_calendar_scrub.
select is_empty(
  $$select r.rol from unnest(array['bot_service', 'dashboard_service', 'scheduler_job', 'platform_admin_readonly',
                                   'purge_job', 'anon', 'authenticated', 'service_role']) r(rol)
     where has_function_privilege(r.rol, 'public.discard_calendar_scrub(uuid, text, text)', 'execute')$$,
  'ningun otro rol puede ejecutar discard_calendar_scrub');
select ok(has_function_privilege('platform_admin', 'public.discard_calendar_scrub(uuid, text, text)', 'execute'),
  'platform_admin puede ejecutar discard_calendar_scrub');
select is(pg_temp.como('purge_job',
  $$select public.discard_calendar_scrub('00000000-0000-0000-0000-0000000000a1', 's1', 'x')::text$$),
  'err 42501', 'purge_job no descarta');
select is(pg_temp.como('platform_admin_readonly',
  $$select public.discard_calendar_scrub('00000000-0000-0000-0000-0000000000a1', 's1', 'x')::text$$),
  'err 42501', 'platform_admin_readonly no descarta');
select is(pg_temp.como('dashboard_service',
  $$select public.discard_calendar_scrub('00000000-0000-0000-0000-0000000000a1', 's1', 'x')::text$$),
  'err 42501', 'dashboard_service no descarta');
select is((select prosecdef from pg_proc where oid = 'public.discard_calendar_scrub(uuid, text, text)'::regprocedure),
  true, 'discard_calendar_scrub es SECURITY DEFINER');

-- Validaciones
select is(pg_temp.como('platform_admin',
  $$select public.discard_calendar_scrub('00000000-0000-0000-0000-0000000000a1', 's1', '   ')::text$$),
  'err 22023', 'motivo vacio se rechaza');
select is(pg_temp.como('platform_admin',
  $$select public.discard_calendar_scrub('00000000-0000-0000-0000-0000000000a1', 's1', null)::text$$),
  'err 22023', 'motivo nulo se rechaza');
select is(pg_temp.como('platform_admin',
  $$select public.discard_calendar_scrub(null, 's1', 'x')::text$$), 'err 22023', 'tenant nulo se rechaza');
select is(pg_temp.como('platform_admin',
  $$select public.discard_calendar_scrub('00000000-0000-0000-0000-0000000000a1', 'no-existe', 'x')::text$$),
  'err P0002', 'un evento que no esta pendiente no se descarta');
select is(pg_temp.como('platform_admin',
  $$select public.discard_calendar_scrub('00000000-0000-0000-0000-0000000000a2', 's1', 'x')::text$$),
  'err P0002', 'el mismo evento en otro tenant no se descarta');

-- Descartar
select is(pg_temp.como('platform_admin',
  $$select public.discard_calendar_scrub('00000000-0000-0000-0000-0000000000a1', 's1', repeat('m', 500))::text$$),
  '', 'descartar se registra (void)');
select is((select length(metadata ->> 'motivo') from public.audit_log where action = 'calendar_scrub_discarded'), 200,
  'el motivo se recorta a 200 caracteres');
select is((select actor_type || '|' || (metadata - 'motivo')::text from public.audit_log
            where action = 'calendar_scrub_discarded'),
  'platform_admin|{"event_id": "s1"}', 'la fila lleva solo el id del evento y el motivo (sin PII), con actor platform_admin');
select is(pg_temp.orden(), 'c1,c2,s2,s3,s4', 'la descartada deja de listarse');
select is(pg_temp.como('platform_admin',
  $$select public.discard_calendar_scrub('00000000-0000-0000-0000-0000000000a1', 's1', 'otra vez')::text$$),
  'err P0002', 'descartar de nuevo una ya descartada falla (no duplica filas)');

-- Un fallo posterior al descarte reabre la limpieza
insert into public.audit_log (tenant_id, actor_type, action, entity, metadata) values
  ('00000000-0000-0000-0000-0000000000a1', 'system', 'calendar_scrub_failed', 'tenants',
   '{"event_id": "s1", "motivo": "otra_vez"}');
select is(pg_temp.orden(), 'c1,c2,s2,s3,s4,s1', 'un fallo posterior al descarte la vuelve a listar');

select is(public.verify_audit_chain('00000000-0000-0000-0000-0000000000a1'), null,
  'la cadena del audit_log del tenant A sigue integra');

select * from finish();
rollback;
