-- El índice viejo audit_log_calendar_scrub_idx ya no existe, el vigente (4 acciones) sí, y las funciones de
-- limpieza de Calendar siguen funcionando sin él (el detalle fino está en turnos_62 y platform_58).
begin;
select plan(8);

do $$
begin
  grant purge_job to current_user with inherit false, set true;
end
$$;

-- Ejecuta p_sql (un solo texto) como p_rol. Devuelve el texto o 'err <sqlstate>'.
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

select ok(to_regclass('public.audit_log_calendar_scrub_idx') is null, 'el índice viejo ya no existe');  -- 1
select ok(to_regclass('public.audit_log_calendar_scrub4_idx') is not null, 'el índice vigente de 4 acciones existe');  -- 2
select is((select count(*) from pg_indexes where schemaname = 'public' and tablename = 'audit_log'  -- 3
             and indexdef like '%calendar_scrub_failed%'), 1::bigint,
  'queda un solo índice parcial de limpiezas de Calendar en audit_log');

insert into public.tenants (id, name, slug) values ('00000000-0000-0000-0000-0000000000a1', 'A', 'scrub-idx-a');
insert into public.audit_log (tenant_id, actor_type, action, entity, metadata) values
  ('00000000-0000-0000-0000-0000000000a1', 'system', 'calendar_scrub_failed', 'tenants',
   '{"event_id": "ev1", "motivo": "http_500"}'),
  ('00000000-0000-0000-0000-0000000000a1', 'system', 'calendar_scrub_failed', 'tenants',
   '{"event_id": "ev2", "motivo": "http_500"}');

select is(pg_temp.como('purge_job', 'select count(*)::text from public.list_calendar_scrubs_pending()'), '2',  -- 4
  'list_calendar_scrubs_pending lista las dos pendientes');
select is(pg_temp.como('purge_job',  -- 5
  $$select public.record_calendar_scrub_result('00000000-0000-0000-0000-0000000000a1', 'ev1', true)::text$$),
  '', 'record_calendar_scrub_result registra la resolución');
select is(pg_temp.como('purge_job',  -- 6
  $$select public.discard_calendar_scrub('00000000-0000-0000-0000-0000000000a1', 'ev2', 'ya no existe')::text$$),
  '', 'discard_calendar_scrub descarta');
select is(pg_temp.como('purge_job', 'select count(*)::text from public.list_calendar_scrubs_pending()'), '0',  -- 7
  'ya no queda nada pendiente');
select is(public.verify_audit_chain('00000000-0000-0000-0000-0000000000a1'), null,  -- 8
  'la cadena del audit_log sigue íntegra');

select * from finish();
rollback;
