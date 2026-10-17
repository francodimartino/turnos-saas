-- Tablas de turnos: RLS, policies y grants por rol (§3.1, §3.2, §3.3, §7.1).
--
-- Cada caso corre con el rol real vía `set role <rol>` (current_user es el rol de login) y el
-- contexto de tenant seteado como lo hace platform_tenancy. Los resultados se guardan en una
-- tabla y las aserciones de pgTAP corren después. El caller con session_user real lo cubren los
-- tests de integración de Python (tests/integracion/test_turnos_rls.py).
--
--   tenants de prueba: A = ...a1, B = ...a2. En los SQL de los casos, {A} y {B} son esos ids.
begin;

-- postgres no es superusuario en Supabase: para `set role` se le otorga membresía con la opción
-- SET dentro de esta transacción (se revierte con el rollback).
do $$
declare
  v_rol text;
begin
  foreach v_rol in array array['bot_service', 'dashboard_service', 'platform_admin',
                               'platform_admin_readonly', 'purge_job', 'scheduler_job'] loop
    execute format('grant %I to current_user with inherit false, set true', v_rol);
  end loop;
end
$$;

create function pg_temp.sustituir(p_sql text) returns text language sql as $$
  select replace(replace(p_sql, '{A}', '00000000-0000-0000-0000-0000000000a1'),
                 '{B}', '00000000-0000-0000-0000-0000000000a2')
$$;

-- Cantidad de filas que ve `p_consulta` como p_rol con contexto de tenant p_tenant
-- ('A', 'B' o null = sin contexto). Si falla: 'err <sqlstate> <mensaje>'.
create function pg_temp.contar(p_rol text, p_tenant text, p_consulta text) returns text
language plpgsql as $$
declare
  v_sql text := pg_temp.sustituir(p_consulta);
  v_ctx text := coalesce(pg_temp.sustituir(case p_tenant when 'A' then '{A}' when 'B' then '{B}' end), '');
  v_n bigint;
begin
  execute format('set role %I', p_rol);
  perform set_config('app.current_tenant_id', v_ctx, true);
  begin
    execute 'select count(*) from (' || v_sql || ') q' into v_n;
    reset role;
    return v_n::text;
  exception when others then
    reset role;
    return 'err ' || sqlstate || ' ' || sqlerrm;
  end;
end
$$;

-- Ejecuta una sentencia (insert/update/delete) como p_rol y devuelve 'ok <filas afectadas>' o
-- 'err <sqlstate> <mensaje>'. Los efectos se revierten siempre (subtransacción abortada a
-- propósito), así los casos son independientes entre sí.
create function pg_temp.ejecutar(p_rol text, p_tenant text, p_sql text) returns text
language plpgsql as $$
declare
  v_sql text := pg_temp.sustituir(p_sql);
  v_ctx text := coalesce(pg_temp.sustituir(case p_tenant when 'A' then '{A}' when 'B' then '{B}' end), '');
  v_n bigint;
begin
  execute format('set role %I', p_rol);
  perform set_config('app.current_tenant_id', v_ctx, true);
  begin
    execute v_sql;
    get diagnostics v_n = row_count;
    raise exception 'revertir' using errcode = 'ZZ001';
  exception when others then
    reset role;
    if sqlstate = 'ZZ001' then
      return 'ok ' || v_n;
    end if;
    return 'err ' || sqlstate || ' ' || sqlerrm;
  end;
end
$$;

-- ---------------------------------------------------------------------------
-- Datos de prueba (como dueño de las tablas, que no pasa por RLS): una fila por tabla y tenant
-- ---------------------------------------------------------------------------
insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000000a1', 'A', 'trls-a'),
  ('00000000-0000-0000-0000-0000000000a2', 'B', 'trls-b');
insert into public.end_customers (id, tenant_id, channel, channel_user_id) values
  ('00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000a1', 'telegram', 'u1'),
  ('00000000-0000-0000-0000-0000000000c2', '00000000-0000-0000-0000-0000000000a2', 'telegram', 'u2');
insert into public.professionals (id, tenant_id, name) values
  ('00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-0000000000a1', 'Prof A'),
  ('00000000-0000-0000-0000-0000000000b2', '00000000-0000-0000-0000-0000000000a2', 'Prof B');
insert into public.services (id, tenant_id, name, duration_minutes, price) values
  ('00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000a1', 'Corte A', 30, 100),
  ('00000000-0000-0000-0000-0000000000e2', '00000000-0000-0000-0000-0000000000a2', 'Corte B', 30, 100);
insert into public.availability_rules (tenant_id, professional_id, day_of_week, start_time, end_time) values
  ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000b1', 1, '09:00', '18:00'),
  ('00000000-0000-0000-0000-0000000000a2', '00000000-0000-0000-0000-0000000000b2', 1, '09:00', '18:00');
insert into public.availability_blocks (tenant_id, professional_id, time_range) values
  ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000b1',
   tstzrange('2030-02-01 00:00+00', '2030-02-02 00:00+00')),
  ('00000000-0000-0000-0000-0000000000a2', '00000000-0000-0000-0000-0000000000b2',
   tstzrange('2030-02-01 00:00+00', '2030-02-02 00:00+00'));
insert into public.professional_services (tenant_id, professional_id, service_id) values
  ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-0000000000e1'),
  ('00000000-0000-0000-0000-0000000000a2', '00000000-0000-0000-0000-0000000000b2', '00000000-0000-0000-0000-0000000000e2');
insert into public.booking_packs (id, tenant_id, end_customer_id, service_id, total_sessions, used_sessions, origin) values
  ('00000000-0000-0000-0000-0000000000d1', '00000000-0000-0000-0000-0000000000a1',
   '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', 5, 1, 'manual_grant'),
  ('00000000-0000-0000-0000-0000000000d2', '00000000-0000-0000-0000-0000000000a2',
   '00000000-0000-0000-0000-0000000000c2', '00000000-0000-0000-0000-0000000000e2', 5, 1, 'manual_grant');
insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status, booking_pack_id) values
  ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1',
   '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1',
   tstzrange('2030-01-01 10:00+00', '2030-01-01 10:30+00'), 'confirmed', '00000000-0000-0000-0000-0000000000d1'),
  ('00000000-0000-0000-0000-0000000000a2', '00000000-0000-0000-0000-0000000000c2',
   '00000000-0000-0000-0000-0000000000e2', '00000000-0000-0000-0000-0000000000b2',
   tstzrange('2030-01-01 10:00+00', '2030-01-01 10:30+00'), 'confirmed', '00000000-0000-0000-0000-0000000000d2');

-- tabla de dominio, una columna cualquiera para los UPDATE sin cambio de valor, y quiénes leen
create temp table tablas_dom (t text, col text);
insert into tablas_dom values
  ('professionals', 'name'), ('services', 'name'), ('availability_rules', 'day_of_week'),
  ('availability_blocks', 'reason'), ('professional_services', 'confirmation_mode'),
  ('booking_packs', 'used_sessions'), ('bookings', 'status');

create temp table casos (n serial, descr text, esperado text, real text);

-- ---------------------------------------------------------------------------
-- RLS activada y policy tenant_isolation en las 7 tablas
-- ---------------------------------------------------------------------------
insert into casos (descr, esperado, real)
select 'RLS activada en ' || d.t, 'true',
       (select c.relrowsecurity::text from pg_class c where c.oid = ('public.' || d.t)::regclass)
  from tablas_dom d;

insert into casos (descr, esperado, real)
select 'policy tenant_isolation existe en ' || d.t, '1',
       (select count(*)::text from pg_policies p
         where p.schemaname = 'public' and p.tablename = d.t and p.policyname = 'tenant_isolation')
  from tablas_dom d;

-- ---------------------------------------------------------------------------
-- Grants de tabla exactos por rol (§3.2)
-- ---------------------------------------------------------------------------
create temp table esperado (rol text, tabla text, privs text);
insert into esperado values
  ('bot_service', 'professionals', 'SELECT'),
  ('bot_service', 'services', 'SELECT'),
  ('bot_service', 'availability_rules', 'SELECT'),
  ('bot_service', 'availability_blocks', 'SELECT'),
  ('bot_service', 'professional_services', 'SELECT'),
  ('bot_service', 'booking_packs', 'SELECT'),
  ('bot_service', 'bookings', 'SELECT,INSERT,UPDATE'),
  ('dashboard_service', 'professionals', 'SELECT,INSERT,UPDATE,DELETE'),
  ('dashboard_service', 'services', 'SELECT,INSERT,UPDATE,DELETE'),
  ('dashboard_service', 'availability_rules', 'SELECT,INSERT,UPDATE,DELETE'),
  ('dashboard_service', 'availability_blocks', 'SELECT,INSERT,UPDATE,DELETE'),
  ('dashboard_service', 'professional_services', 'SELECT,INSERT,UPDATE,DELETE'),
  ('dashboard_service', 'booking_packs', 'SELECT,INSERT,UPDATE'),
  ('dashboard_service', 'bookings', 'SELECT,INSERT,UPDATE'),
  ('platform_admin_readonly', 'professionals', 'SELECT'),
  ('platform_admin_readonly', 'services', 'SELECT'),
  ('platform_admin_readonly', 'availability_rules', 'SELECT'),
  ('platform_admin_readonly', 'availability_blocks', 'SELECT'),
  ('platform_admin_readonly', 'professional_services', 'SELECT'),
  ('platform_admin_readonly', 'booking_packs', 'SELECT'),
  ('platform_admin_readonly', 'bookings', 'SELECT');

insert into casos (descr, esperado, real)
select 'privilegios de tabla: ' || r.rol || ' sobre ' || d.t,
       coalesce(e.privs, ''),
       coalesce((
         select string_agg(p, ',' order by array_position(
                  array['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER'], p))
           from unnest(array['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER']) p
          where has_table_privilege(r.rol, 'public.' || d.t, p)
       ), '')
  from unnest(array['bot_service', 'dashboard_service', 'platform_admin',
                    'platform_admin_readonly', 'purge_job', 'scheduler_job']) as r(rol)
 cross join tablas_dom d
  left join esperado e on e.rol = r.rol and e.tabla = d.t;

-- booking_packs para bot_service: UPDATE solo de la columna used_sessions
insert into casos (descr, esperado, real)
select 'bot_service UPDATE(' || c.col || ') en booking_packs', c.esperado,
       has_column_privilege('bot_service', 'public.booking_packs', c.col, 'UPDATE')::text
  from (values ('used_sessions', 'true'), ('total_sessions', 'false'), ('payment_status', 'false'),
               ('tenant_id', 'false'), ('end_customer_id', 'false'), ('expires_at', 'false')) c(col, esperado);

-- ---------------------------------------------------------------------------
-- Aislamiento de lectura: bot_service y dashboard_service solo ven su tenant
-- ---------------------------------------------------------------------------
insert into casos (descr, esperado, real)
select r.rol || ' ve su fila de ' || d.t, '1',
       pg_temp.contar(r.rol, 'A', format('select 1 from public.%I where tenant_id = ''{A}''', d.t))
  from tablas_dom d cross join (values ('bot_service'), ('dashboard_service')) r(rol);

insert into casos (descr, esperado, real)
select r.rol || ' no ve filas de B en ' || d.t, '0',
       pg_temp.contar(r.rol, 'A', format('select 1 from public.%I where tenant_id = ''{B}''', d.t))
  from tablas_dom d cross join (values ('bot_service'), ('dashboard_service')) r(rol);

insert into casos (descr, esperado, real)
select r.rol || ' sin contexto de tenant no ve nada en ' || d.t || ' (GUC vacío)', '0',
       pg_temp.contar(r.rol, null, format('select 1 from public.%I', d.t))
  from tablas_dom d cross join (values ('bot_service'), ('dashboard_service')) r(rol);

-- ---------------------------------------------------------------------------
-- Escritura sobre filas de B (UPDATE sin cambio de valor): RLS las oculta
-- ---------------------------------------------------------------------------
insert into casos (descr, esperado, real)
select 'dashboard_service no actualiza filas de B en ' || d.t, 'ok 0',
       pg_temp.ejecutar('dashboard_service', 'A',
         format('update public.%I set %I = %I where tenant_id = ''{B}''', d.t, d.col, d.col))
  from tablas_dom d;

insert into casos (descr, esperado, real)
select 'dashboard_service actualiza sus filas en ' || d.t, 'ok 1',
       pg_temp.ejecutar('dashboard_service', 'A',
         format('update public.%I set %I = %I where tenant_id = ''{A}''', d.t, d.col, d.col))
  from tablas_dom d;

insert into casos (descr, esperado, real)
select 'bot_service no actualiza filas de B en ' || d.t, 'ok 0',
       pg_temp.ejecutar('bot_service', 'A',
         format('update public.%I set %I = %I where tenant_id = ''{B}''', d.t, d.col, d.col))
  from tablas_dom d where d.t in ('bookings', 'booking_packs');

insert into casos (descr, esperado, real)
select 'bot_service actualiza sus filas en ' || d.t, 'ok 1',
       pg_temp.ejecutar('bot_service', 'A',
         format('update public.%I set %I = %I where tenant_id = ''{A}''', d.t, d.col, d.col))
  from tablas_dom d where d.t in ('bookings', 'booking_packs');

-- DELETE de dashboard_service: sus filas sí (salvo bookings/booking_packs), las de B no
insert into casos (descr, esperado, real)
select 'dashboard_service borra sus filas en ' || d.t, 'ok 1',
       pg_temp.ejecutar('dashboard_service', 'A', format('delete from public.%I where tenant_id = ''{A}''', d.t))
  from tablas_dom d where d.t in ('availability_rules', 'availability_blocks', 'professional_services');

insert into casos (descr, esperado, real)
select 'dashboard_service no borra filas de B en ' || d.t, 'ok 0',
       pg_temp.ejecutar('dashboard_service', 'A', format('delete from public.%I where tenant_id = ''{B}''', d.t))
  from tablas_dom d where d.t in ('availability_rules', 'availability_blocks', 'professional_services');

insert into casos (descr, esperado, real)
select 'dashboard_service no puede DELETE en ' || d.t, 'err 42501 permission denied%',
       pg_temp.ejecutar('dashboard_service', 'A', format('delete from public.%I', d.t))
  from tablas_dom d where d.t in ('bookings', 'booking_packs');

-- ---------------------------------------------------------------------------
-- INSERT: propio sí, de B no
-- ---------------------------------------------------------------------------
insert into casos (descr, esperado, real) values
  ('dashboard_service inserta professionals propios', 'ok 1',
    pg_temp.ejecutar('dashboard_service', 'A', $q$insert into public.professionals (tenant_id, name) values ('{A}', 'x')$q$)),
  ('dashboard_service no inserta professionals en B', 'err 42501 new row violates%',
    pg_temp.ejecutar('dashboard_service', 'A', $q$insert into public.professionals (tenant_id, name) values ('{B}', 'x')$q$)),
  ('dashboard_service no mueve un professional propio a B', 'err 42501 new row violates%',
    pg_temp.ejecutar('dashboard_service', 'A', $q$update public.professionals set tenant_id = '{B}' where tenant_id = '{A}'$q$)),
  ('dashboard_service inserta services propios', 'ok 1',
    pg_temp.ejecutar('dashboard_service', 'A',
      $q$insert into public.services (tenant_id, name, duration_minutes, price) values ('{A}', 'x', 30, 10)$q$)),
  ('dashboard_service no inserta services en B', 'err 42501 new row violates%',
    pg_temp.ejecutar('dashboard_service', 'A',
      $q$insert into public.services (tenant_id, name, duration_minutes, price) values ('{B}', 'x', 30, 10)$q$)),
  ('dashboard_service inserta availability_rules propias', 'ok 1',
    pg_temp.ejecutar('dashboard_service', 'A',
      $q$insert into public.availability_rules (tenant_id, professional_id, day_of_week, start_time, end_time)
         values ('{A}', '00000000-0000-0000-0000-0000000000b1', 2, '09:00', '12:00')$q$)),
  -- el trigger toma el tenant del profesional, que RLS oculta: falla como referencia inexistente
  ('dashboard_service no inserta availability_rules para un profesional de B', 'err 23503%',
    pg_temp.ejecutar('dashboard_service', 'A',
      $q$insert into public.availability_rules (tenant_id, professional_id, day_of_week, start_time, end_time)
         values ('{B}', '00000000-0000-0000-0000-0000000000b2', 2, '09:00', '12:00')$q$)),
  ('dashboard_service inserta un bloqueo de todo el local propio', 'ok 1',
    pg_temp.ejecutar('dashboard_service', 'A',
      $q$insert into public.availability_blocks (tenant_id, professional_id, time_range)
         values ('{A}', null, tstzrange('2030-03-01 00:00+00', '2030-03-02 00:00+00'))$q$)),
  ('dashboard_service no inserta un bloqueo de todo el local en B', 'err 42501 new row violates%',
    pg_temp.ejecutar('dashboard_service', 'A',
      $q$insert into public.availability_blocks (tenant_id, professional_id, time_range)
         values ('{B}', null, tstzrange('2030-03-01 00:00+00', '2030-03-02 00:00+00'))$q$)),
  ('dashboard_service no inserta availability_blocks para un profesional de B', 'err 23503%',
    pg_temp.ejecutar('dashboard_service', 'A',
      $q$insert into public.availability_blocks (tenant_id, professional_id, time_range)
         values ('{B}', '00000000-0000-0000-0000-0000000000b2', tstzrange('2030-03-01 00:00+00', '2030-03-02 00:00+00'))$q$)),
  ('dashboard_service no inserta professional_services de B', 'err 23503%',
    pg_temp.ejecutar('dashboard_service', 'A',
      $q$insert into public.professional_services (tenant_id, professional_id, service_id)
         values ('{B}', '00000000-0000-0000-0000-0000000000b2', '00000000-0000-0000-0000-0000000000e2')$q$)),
  ('dashboard_service inserta booking_packs propios', 'ok 1',
    pg_temp.ejecutar('dashboard_service', 'A',
      $q$insert into public.booking_packs (tenant_id, end_customer_id, service_id, total_sessions, origin)
         values ('{A}', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', 3, 'manual_grant')$q$)),
  ('dashboard_service no inserta booking_packs para un cliente de B', 'err 23503%',
    pg_temp.ejecutar('dashboard_service', 'A',
      $q$insert into public.booking_packs (tenant_id, end_customer_id, service_id, total_sessions, origin)
         values ('{B}', '00000000-0000-0000-0000-0000000000c2', '00000000-0000-0000-0000-0000000000e2', 3, 'manual_grant')$q$)),
  ('dashboard_service inserta bookings propios', 'ok 1',
    pg_temp.ejecutar('dashboard_service', 'A',
      $q$insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status)
         values ('{A}', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1',
                 '00000000-0000-0000-0000-0000000000b1', tstzrange('2030-01-02 10:00+00', '2030-01-02 10:30+00'), 'confirmed')$q$)),
  ('dashboard_service no inserta bookings en B', 'err 23503%',
    pg_temp.ejecutar('dashboard_service', 'A',
      $q$insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status)
         values ('{B}', '00000000-0000-0000-0000-0000000000c2', '00000000-0000-0000-0000-0000000000e2',
                 '00000000-0000-0000-0000-0000000000b2', tstzrange('2030-01-02 10:00+00', '2030-01-02 10:30+00'), 'confirmed')$q$));

insert into casos (descr, esperado, real) values
  ('bot_service inserta bookings propios (el trigger lee professionals como invocador)', 'ok 1',
    pg_temp.ejecutar('bot_service', 'A',
      $q$insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status)
         values ('{A}', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1',
                 '00000000-0000-0000-0000-0000000000b1', tstzrange('2030-01-02 10:00+00', '2030-01-02 10:30+00'), 'confirmed')$q$)),
  ('bot_service inserta bookings propios usando una sesión del pack', 'ok 1',
    pg_temp.ejecutar('bot_service', 'A',
      $q$insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status, booking_pack_id)
         values ('{A}', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1',
                 '00000000-0000-0000-0000-0000000000b1', tstzrange('2030-01-02 10:00+00', '2030-01-02 10:30+00'), 'confirmed',
                 '00000000-0000-0000-0000-0000000000d1')$q$)),
  ('bot_service no inserta bookings en B', 'err 23503%',
    pg_temp.ejecutar('bot_service', 'A',
      $q$insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status)
         values ('{B}', '00000000-0000-0000-0000-0000000000c2', '00000000-0000-0000-0000-0000000000e2',
                 '00000000-0000-0000-0000-0000000000b2', tstzrange('2030-01-02 10:00+00', '2030-01-02 10:30+00'), 'confirmed')$q$)),
  -- cancelar dispara el trigger que devuelve la sesión: UPDATE de used_sessions como invocador
  ('bot_service cancela un booking con pack (el trigger devuelve la sesión con UPDATE de used_sessions)', 'ok 1',
    pg_temp.ejecutar('bot_service', 'A',
      $q$update public.bookings set status = 'cancelled' where tenant_id = '{A}'$q$)),
  ('dashboard_service cancela un booking con pack (el trigger devuelve la sesión)', 'ok 1',
    pg_temp.ejecutar('dashboard_service', 'A',
      $q$update public.bookings set status = 'cancelled' where tenant_id = '{A}'$q$)),
  ('bot_service no puede DELETE en bookings', 'err 42501 permission denied%',
    pg_temp.ejecutar('bot_service', 'A', 'delete from public.bookings')),
  ('bot_service actualiza used_sessions de su pack', 'ok 1',
    pg_temp.ejecutar('bot_service', 'A', $q$update public.booking_packs set used_sessions = 2 where tenant_id = '{A}'$q$)),
  ('bot_service no actualiza used_sessions del pack de B', 'ok 0',
    pg_temp.ejecutar('bot_service', 'A', $q$update public.booking_packs set used_sessions = 2 where tenant_id = '{B}'$q$)),
  ('bot_service no puede UPDATE total_sessions de booking_packs', 'err 42501 permission denied%',
    pg_temp.ejecutar('bot_service', 'A', $q$update public.booking_packs set total_sessions = 99 where tenant_id = '{A}'$q$)),
  ('bot_service no puede UPDATE payment_status de booking_packs', 'err 42501 permission denied%',
    pg_temp.ejecutar('bot_service', 'A', $q$update public.booking_packs set payment_status = 'paid' where tenant_id = '{A}'$q$)),
  ('bot_service no puede UPDATE tenant_id de booking_packs', 'err 42501 permission denied%',
    pg_temp.ejecutar('bot_service', 'A', $q$update public.booking_packs set tenant_id = '{B}' where tenant_id = '{A}'$q$)),
  ('bot_service no puede INSERT en booking_packs', 'err 42501 permission denied%',
    pg_temp.ejecutar('bot_service', 'A',
      $q$insert into public.booking_packs (tenant_id, end_customer_id, service_id, total_sessions, origin)
         values ('{A}', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', 3, 'manual_grant')$q$)),
  ('bot_service no puede DELETE en booking_packs', 'err 42501 permission denied%',
    pg_temp.ejecutar('bot_service', 'A', 'delete from public.booking_packs'));

-- bot_service: catálogo y disponibilidad solo lectura
insert into casos (descr, esperado, real)
select 'bot_service: ' || o.op || ' en ' || d.t || ' falla con permission denied',
       'err 42501 permission denied%',
       pg_temp.ejecutar('bot_service', 'A',
         case o.op
           when 'INSERT' then format('insert into public.%I default values', d.t)
           when 'UPDATE' then format('update public.%I set %I = %I', d.t, d.col, d.col)
           else format('delete from public.%I', d.t)
         end)
  from tablas_dom d
 cross join (values ('INSERT'), ('UPDATE'), ('DELETE')) o(op)
 where d.t not in ('bookings', 'booking_packs');

-- ---------------------------------------------------------------------------
-- platform_admin_readonly: sin bypass por current_user. Sin contexto de tenant no ve nada; con el contexto de A
-- ve solo las filas de A y ninguna de B. No escribe nada.
-- ---------------------------------------------------------------------------
insert into casos (descr, esperado, real)
select 'platform_admin_readonly sin contexto de tenant no ve filas en ' || d.t, '0',
       pg_temp.contar('platform_admin_readonly', null,
         format('select 1 from public.%I where tenant_id in (''{A}'', ''{B}'')', d.t))
  from tablas_dom d;

insert into casos (descr, esperado, real)
select 'platform_admin_readonly con contexto A ve solo las filas de A en ' || d.t, '1',
       pg_temp.contar('platform_admin_readonly', 'A',
         format('select 1 from public.%I where tenant_id in (''{A}'', ''{B}'')', d.t))
  from tablas_dom d;

insert into casos (descr, esperado, real)
select 'platform_admin_readonly con contexto A no ve filas de B en ' || d.t, '0',
       pg_temp.contar('platform_admin_readonly', 'A',
         format('select 1 from public.%I where tenant_id = ''{B}''', d.t))
  from tablas_dom d;

insert into casos (descr, esperado, real)
select 'platform_admin_readonly: ' || o.op || ' en ' || d.t || ' falla con permission denied',
       'err 42501 permission denied%',
       pg_temp.ejecutar('platform_admin_readonly', 'A',
         case o.op
           when 'INSERT' then format('insert into public.%I default values', d.t)
           when 'UPDATE' then format('update public.%I set %I = %I', d.t, d.col, d.col)
           else format('delete from public.%I', d.t)
         end)
  from tablas_dom d
 cross join (values ('INSERT'), ('UPDATE'), ('DELETE')) o(op);

-- ---------------------------------------------------------------------------
-- platform_admin (sin acceso directo a datos operativos), purge_job y scheduler_job: sin grants
-- ---------------------------------------------------------------------------
insert into casos (descr, esperado, real)
select r.rol || ': ' || o.op || ' en ' || d.t || ' falla con permission denied',
       'err 42501 permission denied%',
       pg_temp.ejecutar(r.rol, 'A',
         case o.op
           when 'SELECT' then format('select * from public.%I', d.t)
           when 'INSERT' then format('insert into public.%I default values', d.t)
           when 'UPDATE' then format('update public.%I set %I = %I', d.t, d.col, d.col)
           else format('delete from public.%I', d.t)
         end)
  from tablas_dom d
 cross join (values ('platform_admin'), ('purge_job'), ('scheduler_job')) r(rol)
 cross join (values ('SELECT'), ('INSERT'), ('UPDATE'), ('DELETE')) o(op);

select plan((select count(*)::int from casos));

select alike(real, esperado, descr) from casos order by n;

select * from finish();
rollback;
