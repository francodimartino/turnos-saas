-- bookings: cambio de horario pendiente de aprobación. Un turno confirmado puede tener un horario propuesto
-- (starts_at_propuesto, ends_at_propuesto, cambio_pendiente_expira_at) sin perder el original; ambos ocupan la
-- agenda del profesional; se resuelve aprobando (el propuesto pasa a ser el horario) o revirtiendo (rechazo o
-- vencimiento); revert_expired_booking_changes() revierte los vencidos para scheduler_job.
begin;
select plan(61);

do $$
declare
  v_rol text;
begin
  foreach v_rol in array array['scheduler_job', 'bot_service', 'dashboard_service'] loop
    execute format('grant %I to current_user with inherit false, set true', v_rol);
  end loop;
end
$$;

create function pg_temp.como_scheduler(p_sql text) returns text
language plpgsql as $$
declare
  v_res text;
begin
  set role scheduler_job;
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

insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000066a1', 'A', 'cambio-a'),
  ('00000000-0000-0000-0000-0000000066a2', 'B', 'cambio-b'),
  ('00000000-0000-0000-0000-0000000066a3', 'S', 'cambio-s');
update public.tenants set status = 'suspended' where id = '00000000-0000-0000-0000-0000000066a3';

insert into public.professionals (id, tenant_id, name) values
  ('00000000-0000-0000-0000-0000000066b1', '00000000-0000-0000-0000-0000000066a1', 'PA'),
  ('00000000-0000-0000-0000-0000000066b2', '00000000-0000-0000-0000-0000000066a2', 'PB'),
  ('00000000-0000-0000-0000-0000000066b3', '00000000-0000-0000-0000-0000000066a3', 'PS');
insert into public.services (id, tenant_id, name, duration_minutes, price) values
  ('00000000-0000-0000-0000-0000000066e1', '00000000-0000-0000-0000-0000000066a1', 'Corte', 30, 100),
  ('00000000-0000-0000-0000-0000000066e2', '00000000-0000-0000-0000-0000000066a2', 'Corte', 30, 100),
  ('00000000-0000-0000-0000-0000000066e3', '00000000-0000-0000-0000-0000000066a3', 'Corte', 30, 100);
insert into public.end_customers (id, tenant_id, channel, channel_user_id) values
  ('00000000-0000-0000-0000-0000000066c1', '00000000-0000-0000-0000-0000000066a1', 'telegram', 'cu1'),
  ('00000000-0000-0000-0000-0000000066c2', '00000000-0000-0000-0000-0000000066a2', 'telegram', 'cu2'),
  ('00000000-0000-0000-0000-0000000066c3', '00000000-0000-0000-0000-0000000066a3', 'telegram', 'cu3');

-- Turnos confirmados del tenant A (profesional b1), uno por día para no chocar entre sí.
insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, status) values
  ('00000000-0000-0000-0000-000000066101', '00000000-0000-0000-0000-0000000066a1', '00000000-0000-0000-0000-0000000066c1', '00000000-0000-0000-0000-0000000066e1', '00000000-0000-0000-0000-0000000066b1', tstzrange('2030-11-01 10:00+00', '2030-11-01 10:30+00'), 'confirmed'),
  ('00000000-0000-0000-0000-000000066102', '00000000-0000-0000-0000-0000000066a1', '00000000-0000-0000-0000-0000000066c1', '00000000-0000-0000-0000-0000000066e1', '00000000-0000-0000-0000-0000000066b1', tstzrange('2030-11-02 10:00+00', '2030-11-02 10:30+00'), 'confirmed'),
  ('00000000-0000-0000-0000-000000066103', '00000000-0000-0000-0000-0000000066a1', '00000000-0000-0000-0000-0000000066c1', '00000000-0000-0000-0000-0000000066e1', '00000000-0000-0000-0000-0000000066b1', tstzrange('2030-11-03 10:00+00', '2030-11-03 10:30+00'), 'confirmed'),
  ('00000000-0000-0000-0000-000000066104', '00000000-0000-0000-0000-0000000066a1', '00000000-0000-0000-0000-0000000066c1', '00000000-0000-0000-0000-0000000066e1', '00000000-0000-0000-0000-0000000066b1', tstzrange('2030-11-04 10:00+00', '2030-11-04 10:30+00'), 'confirmed');
insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, status, pending_expires_at) values
  ('00000000-0000-0000-0000-000000066105', '00000000-0000-0000-0000-0000000066a1', '00000000-0000-0000-0000-0000000066c1', '00000000-0000-0000-0000-0000000066e1', '00000000-0000-0000-0000-0000000066b1', tstzrange('2030-11-05 10:00+00', '2030-11-05 10:30+00'), 'pending', '2030-11-05 08:00+00');

-- Tenant B: mismo horario que el propuesto de A, pero otro tenant y otro profesional.
insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, status) values
  ('00000000-0000-0000-0000-000000066201', '00000000-0000-0000-0000-0000000066a2', '00000000-0000-0000-0000-0000000066c2', '00000000-0000-0000-0000-0000000066e2', '00000000-0000-0000-0000-0000000066b2', tstzrange('2030-11-01 10:00+00', '2030-11-01 10:30+00'), 'confirmed'),
  ('00000000-0000-0000-0000-000000066301', '00000000-0000-0000-0000-0000000066a3', '00000000-0000-0000-0000-0000000066c3', '00000000-0000-0000-0000-0000000066e3', '00000000-0000-0000-0000-0000000066b3', tstzrange('2030-11-01 10:00+00', '2030-11-01 10:30+00'), 'confirmed');

-- Estructura
select has_column('public', 'bookings', 'starts_at_propuesto', 'bookings tiene starts_at_propuesto');  -- 1
select has_column('public', 'bookings', 'cambio_pendiente_expira_at', 'bookings tiene cambio_pendiente_expira_at');  -- 2

-- Proponer: 066101 pide 2030-11-01 14:00-14:30 (expira en el futuro)
select lives_ok(  -- 3
  $$update public.bookings
       set starts_at_propuesto = '2030-11-01 14:00+00', ends_at_propuesto = '2030-11-01 14:30+00',
           cambio_pendiente_expira_at = now() + interval '1 day'
     where id = '00000000-0000-0000-0000-000000066101'$$,
  'un turno confirmado acepta un cambio pendiente');
select is((select time_range from public.bookings where id = '00000000-0000-0000-0000-000000066101'),  -- 4
  tstzrange('2030-11-01 10:00+00', '2030-11-01 10:30+00'), 'el horario original no cambia al proponer');

-- CHECK: las tres columnas juntas
select throws_ok(  -- 5
  $$update public.bookings set starts_at_propuesto = '2030-11-02 14:00+00' where id = '00000000-0000-0000-0000-000000066102'$$,
  '23514', null, 'un cambio pendiente incompleto se rechaza');
select throws_ok(  -- 6
  $$update public.bookings
       set starts_at_propuesto = '2030-11-05 14:00+00', ends_at_propuesto = '2030-11-05 14:30+00',
           cambio_pendiente_expira_at = now() + interval '1 day'
     where id = '00000000-0000-0000-0000-000000066105'$$,
  '23514', null, 'un turno pending no puede tener cambio pendiente');

-- El original sigue ocupando la agenda (exclusion constraint)
select throws_ok(  -- 7
  $$insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status)
    values ('00000000-0000-0000-0000-0000000066a1', '00000000-0000-0000-0000-0000000066c1',
            '00000000-0000-0000-0000-0000000066e1', '00000000-0000-0000-0000-0000000066b1',
            tstzrange('2030-11-01 10:15+00', '2030-11-01 10:45+00'), 'confirmed')$$,
  '23P01', null, 'no se puede reservar encima del horario original durante el cambio pendiente');

-- El propuesto también está reservado
select throws_ok(  -- 8
  $$insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status)
    values ('00000000-0000-0000-0000-0000000066a1', '00000000-0000-0000-0000-0000000066c1',
            '00000000-0000-0000-0000-0000000066e1', '00000000-0000-0000-0000-0000000066b1',
            tstzrange('2030-11-01 14:15+00', '2030-11-01 14:45+00'), 'confirmed')$$,
  '23P01', null, 'no se puede reservar encima del horario propuesto de un cambio pendiente');
select throws_ok(  -- 9
  $$update public.bookings set time_range = tstzrange('2030-11-01 13:45+00', '2030-11-01 14:15+00')
     where id = '00000000-0000-0000-0000-000000066102'$$,
  '23P01', null, 'mover otro turno encima del horario propuesto también se rechaza');
select throws_ok(  -- 10
  $$update public.bookings
       set starts_at_propuesto = '2030-11-01 14:15+00', ends_at_propuesto = '2030-11-01 14:45+00',
           cambio_pendiente_expira_at = now() + interval '1 day'
     where id = '00000000-0000-0000-0000-000000066102'$$,
  '23P01', null, 'dos cambios pendientes no pueden proponer horarios que se pisan');
select throws_ok(  -- 11
  $$update public.bookings
       set starts_at_propuesto = '2030-11-01 10:15+00', ends_at_propuesto = '2030-11-01 10:45+00',
           cambio_pendiente_expira_at = now() + interval '1 day'
     where id = '00000000-0000-0000-0000-000000066102'$$,
  '23P01', null, 'no se puede proponer un horario que pisa el original de otro turno');
select lives_ok(  -- 12
  $$insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status)
    values ('00000000-0000-0000-0000-0000000066a1', '00000000-0000-0000-0000-0000000066c1',
            '00000000-0000-0000-0000-0000000066e1', '00000000-0000-0000-0000-0000000066b1',
            tstzrange('2030-11-01 14:30+00', '2030-11-01 15:00+00'), 'confirmed')$$,
  'un horario adyacente al propuesto sigue libre');
-- aislamiento: el mismo horario en otro tenant no está bloqueado
select lives_ok(  -- 14
  $$insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status)
    values ('00000000-0000-0000-0000-0000000066a2', '00000000-0000-0000-0000-0000000066c2',
            '00000000-0000-0000-0000-0000000066e2', '00000000-0000-0000-0000-0000000066b2',
            tstzrange('2030-11-01 14:00+00', '2030-11-01 14:30+00'), 'confirmed')$$,
  'control: un cambio pendiente no toca la agenda de otro profesional (el aislamiento entre tenants se prueba abajo, con los roles reales)');

-- Mientras está pendiente, el original no se mueve ni la propuesta se edita en el lugar
select throws_ok(  -- 15
  $$update public.bookings set time_range = tstzrange('2030-11-01 11:00+00', '2030-11-01 11:30+00')
     where id = '00000000-0000-0000-0000-000000066101'$$,
  '23514', null, 'el horario original no cambia mientras hay un cambio pendiente');
select throws_ok(  -- 16
  $$update public.bookings
       set starts_at_propuesto = '2030-11-01 16:00+00', ends_at_propuesto = '2030-11-01 16:30+00'
     where id = '00000000-0000-0000-0000-000000066101'$$,
  '23514', null, 'la propuesta pendiente no se modifica en el lugar');

-- Transición válida: confirmed con cambio pendiente -> confirmed con horario nuevo (aprobado)
select lives_ok(  -- 17
  $$update public.bookings
       set time_range = tstzrange('2030-11-01 14:00+00', '2030-11-01 14:30+00'),
           starts_at_propuesto = null, ends_at_propuesto = null, cambio_pendiente_expira_at = null
     where id = '00000000-0000-0000-0000-000000066101'$$,
  'aprobar: el horario nuevo pasa a ser el del turno');
select is((select lower(time_range) from public.bookings where id = '00000000-0000-0000-0000-000000066101'),  -- 18
  '2030-11-01 14:00+00'::timestamptz, 'tras aprobar el turno quedó en el horario nuevo');
select lives_ok(  -- 19
  $$insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status)
    values ('00000000-0000-0000-0000-0000000066a1', '00000000-0000-0000-0000-0000000066c1',
            '00000000-0000-0000-0000-0000000066e1', '00000000-0000-0000-0000-0000000066b1',
            tstzrange('2030-11-01 10:00+00', '2030-11-01 10:30+00'), 'confirmed')$$,
  'tras aprobar el horario original queda libre');

-- Transición válida: -> confirmed con horario original (rechazado)
update public.bookings
   set starts_at_propuesto = '2030-11-02 15:00+00', ends_at_propuesto = '2030-11-02 15:30+00',
       cambio_pendiente_expira_at = now() + interval '1 day'
 where id = '00000000-0000-0000-0000-000000066102';
select lives_ok(  -- 20
  $$update public.bookings
       set starts_at_propuesto = null, ends_at_propuesto = null, cambio_pendiente_expira_at = null
     where id = '00000000-0000-0000-0000-000000066102'$$,
  'rechazar: el turno conserva su horario original');
select is((select lower(time_range) from public.bookings where id = '00000000-0000-0000-0000-000000066102'),  -- 21
  '2030-11-02 10:00+00'::timestamptz, 'tras rechazar el horario original sigue igual');
select lives_ok(  -- 22
  $$insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status)
    values ('00000000-0000-0000-0000-0000000066a1', '00000000-0000-0000-0000-0000000066c1',
            '00000000-0000-0000-0000-0000000066e1', '00000000-0000-0000-0000-0000000066b1',
            tstzrange('2030-11-02 15:00+00', '2030-11-02 15:30+00'), 'confirmed')$$,
  'tras rechazar el horario propuesto queda libre');

-- Transición inválida: resolver con un horario que no es ni el propuesto ni el original
update public.bookings
   set starts_at_propuesto = '2030-11-03 15:00+00', ends_at_propuesto = '2030-11-03 15:30+00',
       cambio_pendiente_expira_at = now() + interval '1 day'
 where id = '00000000-0000-0000-0000-000000066103';
select throws_ok(  -- 23
  $$update public.bookings
       set time_range = tstzrange('2030-11-03 17:00+00', '2030-11-03 17:30+00'),
           starts_at_propuesto = null, ends_at_propuesto = null, cambio_pendiente_expira_at = null
     where id = '00000000-0000-0000-0000-000000066103'$$,
  '23514', null, 'resolver con un horario que no es ni el propuesto ni el original se rechaza');

-- Cancelar un turno con cambio pendiente descarta la propuesta y libera el horario
select lives_ok(  -- 24
  $$update public.bookings set status = 'cancelled' where id = '00000000-0000-0000-0000-000000066103'$$,
  'confirmed con cambio pendiente -> cancelled es válida');
select is((select starts_at_propuesto is null and cambio_pendiente_expira_at is null  -- 25
             from public.bookings where id = '00000000-0000-0000-0000-000000066103'),
  true, 'al cancelar se descarta el cambio pendiente');

-- Vencimiento: 066104 con cambio vencido, 066201 (tenant B) y 066301 (tenant suspendido) también
update public.bookings
   set starts_at_propuesto = '2030-11-04 15:00+00', ends_at_propuesto = '2030-11-04 15:30+00',
       cambio_pendiente_expira_at = now() - interval '1 minute'
 where id = '00000000-0000-0000-0000-000000066104';
update public.bookings
   set starts_at_propuesto = '2030-11-01 16:00+00', ends_at_propuesto = '2030-11-01 16:30+00',
       cambio_pendiente_expira_at = now() - interval '1 minute'
 where id in ('00000000-0000-0000-0000-000000066201', '00000000-0000-0000-0000-000000066301');
-- uno vigente (no vence)
update public.bookings
   set starts_at_propuesto = '2030-11-02 16:00+00', ends_at_propuesto = '2030-11-02 16:30+00',
       cambio_pendiente_expira_at = now() + interval '1 hour'
 where id = '00000000-0000-0000-0000-000000066102';

select ok(  -- 26
  has_function_privilege('scheduler_job', 'public.revert_expired_booking_changes()', 'execute')
  and not has_function_privilege('bot_service', 'public.revert_expired_booking_changes()', 'execute')
  and not has_function_privilege('dashboard_service', 'public.revert_expired_booking_changes()', 'execute'),
  'revert_expired_booking_changes: solo scheduler_job');

create temp table corrida as
select pg_temp.como_scheduler($q$
  select string_agg(r.booking_id::text || '|' || r.tenant_id::text || '|' || coalesce(r.channel_user_id, '-') || '|'
                    || r.notify::text || '|' || coalesce(r.end_customer_id::text, '-') || '|'
                    || to_char(r.starts_at at time zone 'UTC', 'MM-DD HH24:MI') || '|'
                    || to_char(r.starts_at_propuesto at time zone 'UTC', 'MM-DD HH24:MI') || '|' || r.service_name,
                    ';' order by r.booking_id)
    from public.revert_expired_booking_changes() r
   where r.booking_id::text like '00000000-0000-0000-0000-00000006%'
$q$) as res;
select is((select res from corrida),  -- 27
  '00000000-0000-0000-0000-000000066104|00000000-0000-0000-0000-0000000066a1|cu1|true|00000000-0000-0000-0000-0000000066c1|11-04 10:00|11-04 15:00|Corte;'
  || '00000000-0000-0000-0000-000000066201|00000000-0000-0000-0000-0000000066a2|cu2|true|00000000-0000-0000-0000-0000000066c2|11-01 10:00|11-01 16:00|Corte;'
  || '00000000-0000-0000-0000-000000066301|00000000-0000-0000-0000-0000000066a3|-|false|-|11-01 10:00|11-01 16:00|Corte',
  'revierte solo los vencidos, cada uno con su tenant; el tenant suspendido no se avisa ni expone datos del cliente');
select is((select lower(time_range) from public.bookings where id = '00000000-0000-0000-0000-000000066104'),  -- 28
  '2030-11-04 10:00+00'::timestamptz, 'el turno vencido conserva el horario original');
select is((select cambio_pendiente_expira_at is not null from public.bookings  -- 29
            where id = '00000000-0000-0000-0000-000000066102'), true,
  'un cambio todavía vigente no se revierte');
select is(pg_temp.como_scheduler($q$  -- 30
  select count(*)::text from public.revert_expired_booking_changes() r
   where r.booking_id::text like '00000000-0000-0000-0000-00000006%'$q$), '0',
  'una segunda corrida no vuelve a revertir ni avisar');


-- ===========================================================================================================
-- Roles reales (bot_service y dashboard_service, con app.current_tenant_id): aislamiento entre tenants, y proponer,
-- chocar y resolver como los ejecuta producción (el trigger es security invoker y la RLS aplica).
-- ===========================================================================================================
create function pg_temp.como_n(p_rol text, p_ctx uuid, p_sql text) returns text
language plpgsql as $$
declare
  v_n bigint;
begin
  perform set_config('app.current_tenant_id', coalesce(p_ctx::text, ''), true);
  execute format('set role %I', p_rol);
  begin
    if p_sql ilike 'select%' then
      execute 'select count(*) from (' || p_sql || ') q' into v_n;
    else
      execute p_sql;
      get diagnostics v_n = row_count;
    end if;
  exception when others then
    reset role;
    return 'err ' || sqlstate;
  end;
  reset role;
  return v_n::text;
end
$$;

insert into public.professionals (id, tenant_id, name) values ('00000000-0000-0000-0000-0000000066b4', '00000000-0000-0000-0000-0000000066a1', 'PA2');
insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, status) values
  ('00000000-0000-0000-0000-000000066202', '00000000-0000-0000-0000-0000000066a2', '00000000-0000-0000-0000-0000000066c2', '00000000-0000-0000-0000-0000000066e2', '00000000-0000-0000-0000-0000000066b2', tstzrange('2030-11-09 10:00+00', '2030-11-09 10:30+00'), 'confirmed'),
  ('00000000-0000-0000-0000-000000066203', '00000000-0000-0000-0000-0000000066a2', '00000000-0000-0000-0000-0000000066c2', '00000000-0000-0000-0000-0000000066e2', '00000000-0000-0000-0000-0000000066b2', tstzrange('2030-11-09 12:00+00', '2030-11-09 12:30+00'), 'confirmed');
update public.bookings
   set starts_at_propuesto = '2030-11-09 15:00+00', ends_at_propuesto = '2030-11-09 15:30+00',
       cambio_pendiente_expira_at = now() + interval '1 day'
 where id = '00000000-0000-0000-0000-000000066202';

-- Aislamiento: con el contexto del tenant A, cada rol no ve ni toca los turnos del tenant B
create temp table aislamiento as
select r.rol, o.op, pg_temp.como_n(r.rol, '00000000-0000-0000-0000-0000000066a1', o.sql) as res
  from (values ('bot_service'), ('dashboard_service')) r(rol)
 cross join (values
   ('leer', $q$select 1 from public.bookings where id in ('00000000-0000-0000-0000-000000066202', '00000000-0000-0000-0000-000000066203')$q$),
   ('proponer', $q$update public.bookings set starts_at_propuesto = '2030-11-09 16:00+00',
        ends_at_propuesto = '2030-11-09 16:30+00', cambio_pendiente_expira_at = now() + interval '1 day'
      where id = '00000000-0000-0000-0000-000000066203'$q$),
   ('aprobar', $q$update public.bookings set time_range = tstzrange('2030-11-09 15:00+00', '2030-11-09 15:30+00'),
        starts_at_propuesto = null, ends_at_propuesto = null, cambio_pendiente_expira_at = null
      where id = '00000000-0000-0000-0000-000000066202'$q$),
   ('rechazar', $q$update public.bookings set starts_at_propuesto = null, ends_at_propuesto = null,
        cambio_pendiente_expira_at = null where id = '00000000-0000-0000-0000-000000066202'$q$)
 ) o(op, sql);

select is((select count(*)::int from aislamiento), 8, 'aislamiento: se probaron 4 operaciones con cada uno de los 2 roles');
select is_empty($$select rol, op, res from aislamiento where res <> '0'$$,
  'aislamiento: con el contexto del tenant A ningún rol lee, propone, aprueba ni rechaza el cambio de un turno del tenant B (0 filas)');
select is((select count(*)::int from public.bookings
            where (id = '00000000-0000-0000-0000-000000066202' and lower(time_range) = '2030-11-09 10:00+00'
                   and starts_at_propuesto = '2030-11-09 15:00+00')
               or (id = '00000000-0000-0000-0000-000000066203' and starts_at_propuesto is null
                   and lower(time_range) = '2030-11-09 12:00+00')), 2,
  'aislamiento: los turnos del tenant B quedaron intactos');
insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, status) values
  ('00000000-0000-0000-0000-000000066041', '00000000-0000-0000-0000-0000000066a1', '00000000-0000-0000-0000-0000000066c1', '00000000-0000-0000-0000-0000000066e1', '00000000-0000-0000-0000-0000000066b4', tstzrange('2030-11-14 10:00+00', '2030-11-14 10:30+00'), 'confirmed'),
  ('00000000-0000-0000-0000-000000066042', '00000000-0000-0000-0000-0000000066a1', '00000000-0000-0000-0000-0000000066c1', '00000000-0000-0000-0000-0000000066e1', '00000000-0000-0000-0000-0000000066b4', tstzrange('2030-11-14 14:00+00', '2030-11-14 14:30+00'), 'confirmed'),
  ('00000000-0000-0000-0000-000000066043', '00000000-0000-0000-0000-0000000066a1', '00000000-0000-0000-0000-0000000066c1', '00000000-0000-0000-0000-0000000066e1', '00000000-0000-0000-0000-0000000066b4', tstzrange('2030-11-14 16:00+00', '2030-11-14 16:30+00'), 'confirmed');
select is(pg_temp.como_n('bot_service', '00000000-0000-0000-0000-0000000066a1', $q$update public.bookings set starts_at_propuesto = '2030-11-14 12:00+00', ends_at_propuesto = '2030-11-14 12:30+00', cambio_pendiente_expira_at = now() + interval '1 day' where id = '00000000-0000-0000-0000-000000066041'$q$), '1', 'bot_service: proponer');
select is(pg_temp.como_n('bot_service', '00000000-0000-0000-0000-0000000066a1', $q$insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status) values ('00000000-0000-0000-0000-0000000066a1', '00000000-0000-0000-0000-0000000066c1', '00000000-0000-0000-0000-0000000066e1', '00000000-0000-0000-0000-0000000066b4', tstzrange('2030-11-14 12:15+00', '2030-11-14 12:45+00'), 'confirmed')$q$), 'err 23P01', 'bot_service: chocar con la propuesta');
select is(pg_temp.como_n('bot_service', '00000000-0000-0000-0000-0000000066a1', $q$update public.bookings set starts_at_propuesto = '2030-11-14 14:15+00', ends_at_propuesto = '2030-11-14 14:45+00', cambio_pendiente_expira_at = now() + interval '1 day' where id = '00000000-0000-0000-0000-000000066043'$q$), 'err 23P01', 'bot_service: proponer encima de otro turno');
select is(pg_temp.como_n('bot_service', '00000000-0000-0000-0000-0000000066a1', $q$update public.bookings set time_range = tstzrange('2030-11-14 12:00+00', '2030-11-14 12:30+00'), starts_at_propuesto = null, ends_at_propuesto = null, cambio_pendiente_expira_at = null where id = '00000000-0000-0000-0000-000000066041'$q$), '1', 'bot_service: aprobar');
select is(pg_temp.como_n('bot_service', '00000000-0000-0000-0000-0000000066a1', $q$update public.bookings set starts_at_propuesto = '2030-11-14 15:00+00', ends_at_propuesto = '2030-11-14 15:30+00', cambio_pendiente_expira_at = now() + interval '1 day' where id = '00000000-0000-0000-0000-000000066043'$q$), '1', 'bot_service: proponer otro');
select is(pg_temp.como_n('bot_service', '00000000-0000-0000-0000-0000000066a1', $q$update public.bookings set starts_at_propuesto = null, ends_at_propuesto = null, cambio_pendiente_expira_at = null where id = '00000000-0000-0000-0000-000000066043'$q$), '1', 'bot_service: rechazar');
select is((select (select to_char(lower(time_range) at time zone 'UTC', 'DD HH24:MI') from public.bookings where id = '00000000-0000-0000-0000-000000066041') || '|' ||
                   (select to_char(lower(time_range) at time zone 'UTC', 'DD HH24:MI') from public.bookings where id = '00000000-0000-0000-0000-000000066043')),
  '14 12:00|14 16:00', 'bot_service: el aprobado quedó en el horario nuevo y el rechazado en el original');
insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, status) values
  ('00000000-0000-0000-0000-000000066051', '00000000-0000-0000-0000-0000000066a1', '00000000-0000-0000-0000-0000000066c1', '00000000-0000-0000-0000-0000000066e1', '00000000-0000-0000-0000-0000000066b4', tstzrange('2030-11-15 10:00+00', '2030-11-15 10:30+00'), 'confirmed'),
  ('00000000-0000-0000-0000-000000066052', '00000000-0000-0000-0000-0000000066a1', '00000000-0000-0000-0000-0000000066c1', '00000000-0000-0000-0000-0000000066e1', '00000000-0000-0000-0000-0000000066b4', tstzrange('2030-11-15 14:00+00', '2030-11-15 14:30+00'), 'confirmed'),
  ('00000000-0000-0000-0000-000000066053', '00000000-0000-0000-0000-0000000066a1', '00000000-0000-0000-0000-0000000066c1', '00000000-0000-0000-0000-0000000066e1', '00000000-0000-0000-0000-0000000066b4', tstzrange('2030-11-15 16:00+00', '2030-11-15 16:30+00'), 'confirmed');
select is(pg_temp.como_n('dashboard_service', '00000000-0000-0000-0000-0000000066a1', $q$update public.bookings set starts_at_propuesto = '2030-11-15 12:00+00', ends_at_propuesto = '2030-11-15 12:30+00', cambio_pendiente_expira_at = now() + interval '1 day' where id = '00000000-0000-0000-0000-000000066051'$q$), '1', 'dashboard_service: proponer');
select is(pg_temp.como_n('dashboard_service', '00000000-0000-0000-0000-0000000066a1', $q$insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status) values ('00000000-0000-0000-0000-0000000066a1', '00000000-0000-0000-0000-0000000066c1', '00000000-0000-0000-0000-0000000066e1', '00000000-0000-0000-0000-0000000066b4', tstzrange('2030-11-15 12:15+00', '2030-11-15 12:45+00'), 'confirmed')$q$), 'err 23P01', 'dashboard_service: chocar con la propuesta');
select is(pg_temp.como_n('dashboard_service', '00000000-0000-0000-0000-0000000066a1', $q$update public.bookings set starts_at_propuesto = '2030-11-15 14:15+00', ends_at_propuesto = '2030-11-15 14:45+00', cambio_pendiente_expira_at = now() + interval '1 day' where id = '00000000-0000-0000-0000-000000066053'$q$), 'err 23P01', 'dashboard_service: proponer encima de otro turno');
select is(pg_temp.como_n('dashboard_service', '00000000-0000-0000-0000-0000000066a1', $q$update public.bookings set time_range = tstzrange('2030-11-15 12:00+00', '2030-11-15 12:30+00'), starts_at_propuesto = null, ends_at_propuesto = null, cambio_pendiente_expira_at = null where id = '00000000-0000-0000-0000-000000066051'$q$), '1', 'dashboard_service: aprobar');
select is(pg_temp.como_n('dashboard_service', '00000000-0000-0000-0000-0000000066a1', $q$update public.bookings set starts_at_propuesto = '2030-11-15 15:00+00', ends_at_propuesto = '2030-11-15 15:30+00', cambio_pendiente_expira_at = now() + interval '1 day' where id = '00000000-0000-0000-0000-000000066053'$q$), '1', 'dashboard_service: proponer otro');
select is(pg_temp.como_n('dashboard_service', '00000000-0000-0000-0000-0000000066a1', $q$update public.bookings set starts_at_propuesto = null, ends_at_propuesto = null, cambio_pendiente_expira_at = null where id = '00000000-0000-0000-0000-000000066053'$q$), '1', 'dashboard_service: rechazar');
select is((select (select to_char(lower(time_range) at time zone 'UTC', 'DD HH24:MI') from public.bookings where id = '00000000-0000-0000-0000-000000066051') || '|' ||
                   (select to_char(lower(time_range) at time zone 'UTC', 'DD HH24:MI') from public.bookings where id = '00000000-0000-0000-0000-000000066053')),
  '15 12:00|15 16:00', 'dashboard_service: el aprobado quedó en el horario nuevo y el rechazado en el original');
insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, status) values
  ('00000000-0000-0000-0000-000000066106', '00000000-0000-0000-0000-0000000066a1', '00000000-0000-0000-0000-0000000066c1', '00000000-0000-0000-0000-0000000066e1', '00000000-0000-0000-0000-0000000066b1', tstzrange('2030-11-06 10:00+00', '2030-11-06 10:30+00'), 'confirmed'),
  ('00000000-0000-0000-0000-000000066111', '00000000-0000-0000-0000-0000000066a1', '00000000-0000-0000-0000-0000000066c1', '00000000-0000-0000-0000-0000000066e1', '00000000-0000-0000-0000-0000000066b4', tstzrange('2030-11-06 15:15+00', '2030-11-06 15:45+00'), 'confirmed'),
  ('00000000-0000-0000-0000-000000066107', '00000000-0000-0000-0000-0000000066a1', '00000000-0000-0000-0000-0000000066c1', '00000000-0000-0000-0000-0000000066e1', '00000000-0000-0000-0000-0000000066b1', tstzrange('2030-11-07 10:00+00', '2030-11-07 10:30+00'), 'cancelled'),
  ('00000000-0000-0000-0000-000000066109', '00000000-0000-0000-0000-0000000066a1', '00000000-0000-0000-0000-0000000066c1', '00000000-0000-0000-0000-0000000066e1', '00000000-0000-0000-0000-0000000066b1', tstzrange('2030-11-07 12:00+00', '2030-11-07 12:30+00'), 'completed'),
  ('00000000-0000-0000-0000-000000066110', '00000000-0000-0000-0000-0000000066a1', '00000000-0000-0000-0000-0000000066c1', '00000000-0000-0000-0000-0000000066e1', '00000000-0000-0000-0000-0000000066b1', tstzrange('2030-11-10 10:00+00', '2030-11-10 10:30+00'), 'confirmed'),
  ('00000000-0000-0000-0000-000000066108', '00000000-0000-0000-0000-0000000066a1', '00000000-0000-0000-0000-0000000066c1', '00000000-0000-0000-0000-0000000066e1', '00000000-0000-0000-0000-0000000066b1', tstzrange('2030-11-08 10:00+00', '2030-11-08 10:30+00'), 'confirmed');

-- Ramas del trigger y de los checks (dueño de las migraciones)
select throws_ok($$insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status,
    starts_at_propuesto, ends_at_propuesto, cambio_pendiente_expira_at)
  values ('00000000-0000-0000-0000-0000000066a1', '00000000-0000-0000-0000-0000000066c1', '00000000-0000-0000-0000-0000000066e1', '00000000-0000-0000-0000-0000000066b1', tstzrange('2030-11-20 10:00+00', '2030-11-20 10:30+00'), 'confirmed',
          '2030-11-20 12:00+00', '2030-11-20 12:30+00', now() + interval '1 day')$$,
  '23514', null, 'un turno nuevo no nace con un cambio pendiente');
select throws_ok($$update public.bookings set starts_at_propuesto = '2030-11-06 12:00+00',
    ends_at_propuesto = '2030-11-06 12:00+00', cambio_pendiente_expira_at = now() + interval '1 day'
  where id = '00000000-0000-0000-0000-000000066106'$$, '23514', null, 'la propuesta con fin igual al inicio se rechaza');
select throws_ok($$update public.bookings set starts_at_propuesto = '2030-11-06 12:30+00',
    ends_at_propuesto = '2030-11-06 12:00+00', cambio_pendiente_expira_at = now() + interval '1 day'
  where id = '00000000-0000-0000-0000-000000066106'$$, '23514', null, 'la propuesta con fin anterior al inicio se rechaza');
select throws_ok($$update public.bookings set starts_at_propuesto = '2030-11-07 14:00+00',
    ends_at_propuesto = '2030-11-07 14:30+00', cambio_pendiente_expira_at = now() + interval '1 day'
  where id = '00000000-0000-0000-0000-000000066107'$$, '23514', null, 'un turno cancelled no acepta un cambio pendiente');
select throws_ok($$update public.bookings set starts_at_propuesto = '2030-11-07 14:00+00',
    ends_at_propuesto = '2030-11-07 14:30+00', cambio_pendiente_expira_at = now() + interval '1 day'
  where id = '00000000-0000-0000-0000-000000066109'$$, '23514', null, 'un turno completed no acepta un cambio pendiente');

update public.bookings set starts_at_propuesto = '2030-11-10 15:00+00', ends_at_propuesto = '2030-11-10 15:30+00',
       cambio_pendiente_expira_at = now() + interval '1 day' where id = '00000000-0000-0000-0000-000000066110';
select lives_ok($$update public.bookings set status = 'completed' where id = '00000000-0000-0000-0000-000000066110'$$,
  'confirmed con cambio pendiente -> completed es válida');
select is((select starts_at_propuesto is null and ends_at_propuesto is null and cambio_pendiente_expira_at is null
             from public.bookings where id = '00000000-0000-0000-0000-000000066110'), true, 'al completarse se descarta el cambio pendiente');

-- cambio de profesional con propuesta vigente: se revalida contra la agenda del nuevo profesional
update public.bookings set starts_at_propuesto = '2030-11-06 15:00+00', ends_at_propuesto = '2030-11-06 15:30+00',
       cambio_pendiente_expira_at = now() + interval '1 day' where id = '00000000-0000-0000-0000-000000066106';
select throws_ok($$update public.bookings set professional_id = '00000000-0000-0000-0000-0000000066b4' where id = '00000000-0000-0000-0000-000000066106'$$,
  '23P01', null, 'cambiar de profesional revalida la propuesta contra la agenda del nuevo profesional');

-- un turno cancelled no ocupa agenda: ni al crearse ni al moverse encima de un horario propuesto
select lives_ok($$insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status)
  values ('00000000-0000-0000-0000-0000000066a1', '00000000-0000-0000-0000-0000000066c1', '00000000-0000-0000-0000-0000000066e1', '00000000-0000-0000-0000-0000000066b1', tstzrange('2030-11-06 15:00+00', '2030-11-06 15:30+00'), 'cancelled')$$,
  'un turno cancelled se puede crear encima de un horario propuesto');
select lives_ok($$update public.bookings set time_range = tstzrange('2030-11-06 15:00+00', '2030-11-06 15:30+00')
  where id = '00000000-0000-0000-0000-000000066107'$$, 'un turno cancelled se puede mover encima de un horario propuesto');

-- cambio vencido y no revertido: no bloquea la agenda y no se aprueba
update public.bookings set starts_at_propuesto = '2030-11-08 15:00+00', ends_at_propuesto = '2030-11-08 15:30+00',
       cambio_pendiente_expira_at = now() - interval '1 minute' where id = '00000000-0000-0000-0000-000000066108';
select lives_ok($$insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status)
  values ('00000000-0000-0000-0000-0000000066a1', '00000000-0000-0000-0000-0000000066c1', '00000000-0000-0000-0000-0000000066e1', '00000000-0000-0000-0000-0000000066b1', tstzrange('2030-11-08 15:00+00', '2030-11-08 15:30+00'), 'confirmed')$$,
  'un cambio vencido no bloquea la agenda');
select throws_ok($$update public.bookings set time_range = tstzrange('2030-11-08 15:00+00', '2030-11-08 15:30+00'),
    starts_at_propuesto = null, ends_at_propuesto = null, cambio_pendiente_expira_at = null where id = '00000000-0000-0000-0000-000000066108'$$,
  '23514', null, 'un cambio vencido no se puede aprobar');
select lives_ok($$update public.bookings set starts_at_propuesto = null, ends_at_propuesto = null,
    cambio_pendiente_expira_at = null where id = '00000000-0000-0000-0000-000000066108'$$, 'un cambio vencido sí se puede rechazar');

-- aviso en el outbox, en la misma sentencia que la reversión (las dos filas vencidas de la primera corrida)
select is((select count(*)::int from public.notification_outbox where template = 'cambio_horario_vencido'), 2,
  'revert_expired_booking_changes encoló un aviso por cada cambio revertido con destinatario (no para el tenant suspendido)');
select is((select status || ':' || attempts || ':' || (params ->> 'booking_id')
             from public.notification_outbox where template = 'cambio_horario_vencido'
              and tenant_id = '00000000-0000-0000-0000-0000000066a1'),
  'pending:0:00000000-0000-0000-0000-000000066104', 'la fila nace pending, sin intentos y con el turno en params');

select * from finish();
rollback;
