-- Roles web_reservas y enviador: aislamiento entre negocios con los roles reales.
-- Con el contexto del negocio A, ninguno de los dos roles ve ni escribe filas del negocio B en ninguna tabla que
-- tenga (web_reservas) ni por sus funciones (enviador, que no tiene tablas); sin contexto, web_reservas no ve
-- ninguna fila de ninguna tabla y solo resuelve el slug con resolver_slug_tenant.
-- Por diseño claim_notification_outbox no recibe contexto de tenant: el enviador reclama filas de todos los negocios
-- y recién después fija el contexto de cada fila para resolver, registrar y completar.
begin;

do $$
declare
  v_rol text;
begin
  foreach v_rol in array array['web_reservas', 'enviador'] loop
    execute format('grant %I to current_user with inherit false, set true', v_rol);
  end loop;
end
$$;

-- Ejecuta p_sql como p_rol con el tenant p_ctx ('' = sin contexto). Un select devuelve su único valor como texto
-- ('<null>' si es nulo); cualquier otra sentencia devuelve 'ok <filas>'. Un error devuelve 'err <sqlstate>'.
create function pg_temp.como(p_rol text, p_ctx text, p_sql text) returns text
language plpgsql as $$
declare
  v text;
  v_filas bigint;
begin
  execute format('set role %I', p_rol);
  perform set_config('app.current_tenant_id', coalesce(p_ctx, ''), true);
  begin
    if lower(left(ltrim(p_sql), 6)) = 'select' then
      execute p_sql into v;
      reset role;
      return coalesce(v, '<null>');
    end if;
    execute p_sql;
    get diagnostics v_filas = row_count;
    reset role;
    return 'ok ' || v_filas;
  exception when others then
    reset role;
    return 'err ' || sqlstate;
  end;
end
$$;

-- ---------------------------------------------------------------------------
-- Datos: dos negocios con una fila en cada tabla del rol de la web
-- ---------------------------------------------------------------------------
insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000079a1', 'A', 'aislamiento-a'),
  ('00000000-0000-0000-0000-0000000079b1', 'B', 'aislamiento-b');
insert into public.professionals (id, tenant_id, name) values
  ('00000000-0000-0000-0000-0000000079d1', '00000000-0000-0000-0000-0000000079a1', 'Ana'),
  ('00000000-0000-0000-0000-0000000079d2', '00000000-0000-0000-0000-0000000079b1', 'Beto');
insert into public.services (id, tenant_id, name, duration_minutes, price) values
  ('00000000-0000-0000-0000-0000000079e1', '00000000-0000-0000-0000-0000000079a1', 'Corte', 30, 100),
  ('00000000-0000-0000-0000-0000000079e2', '00000000-0000-0000-0000-0000000079b1', 'Tintura', 30, 100);
insert into public.professional_services (tenant_id, professional_id, service_id) values
  ('00000000-0000-0000-0000-0000000079a1', '00000000-0000-0000-0000-0000000079d1', '00000000-0000-0000-0000-0000000079e1'),
  ('00000000-0000-0000-0000-0000000079b1', '00000000-0000-0000-0000-0000000079d2', '00000000-0000-0000-0000-0000000079e2');
insert into public.availability_rules (tenant_id, professional_id, day_of_week, start_time, end_time) values
  ('00000000-0000-0000-0000-0000000079a1', '00000000-0000-0000-0000-0000000079d1', 1, '09:00', '18:00'),
  ('00000000-0000-0000-0000-0000000079b1', '00000000-0000-0000-0000-0000000079d2', 1, '09:00', '18:00');
insert into public.availability_blocks (tenant_id, professional_id, time_range) values
  ('00000000-0000-0000-0000-0000000079a1', '00000000-0000-0000-0000-0000000079d1',
   tstzrange('2031-03-01 10:00+00', '2031-03-01 11:00+00')),
  ('00000000-0000-0000-0000-0000000079b1', '00000000-0000-0000-0000-0000000079d2',
   tstzrange('2031-03-01 10:00+00', '2031-03-01 11:00+00'));
insert into public.end_customers (id, tenant_id, name) values
  ('00000000-0000-0000-0000-0000000079c1', '00000000-0000-0000-0000-0000000079a1', 'Clienta A'),
  ('00000000-0000-0000-0000-0000000079c2', '00000000-0000-0000-0000-0000000079b1', 'Cliente B');
insert into public.canales_cliente (tenant_id, end_customer_id, canal, identificador, verificado_at) values
  ('00000000-0000-0000-0000-0000000079a1', '00000000-0000-0000-0000-0000000079c1', 'email', 'ca@a.test', now()),
  ('00000000-0000-0000-0000-0000000079b1', '00000000-0000-0000-0000-0000000079c2', 'email', 'cb@b.test', now());
insert into public.booking_packs (id, tenant_id, end_customer_id, service_id, total_sessions, used_sessions, origin) values
  ('00000000-0000-0000-0000-0000000079f3', '00000000-0000-0000-0000-0000000079a1',
   '00000000-0000-0000-0000-0000000079c1', '00000000-0000-0000-0000-0000000079e1', 5, 1, 'manual_grant'),
  ('00000000-0000-0000-0000-0000000079f4', '00000000-0000-0000-0000-0000000079b1',
   '00000000-0000-0000-0000-0000000079c2', '00000000-0000-0000-0000-0000000079e2', 5, 1, 'manual_grant');
insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, status) values
  ('00000000-0000-0000-0000-0000000079f1', '00000000-0000-0000-0000-0000000079a1',
   '00000000-0000-0000-0000-0000000079c1', '00000000-0000-0000-0000-0000000079e1',
   '00000000-0000-0000-0000-0000000079d1', tstzrange('2031-03-02 10:00+00', '2031-03-02 10:30+00'), 'confirmed'),
  ('00000000-0000-0000-0000-0000000079f2', '00000000-0000-0000-0000-0000000079b1',
   '00000000-0000-0000-0000-0000000079c2', '00000000-0000-0000-0000-0000000079e2',
   '00000000-0000-0000-0000-0000000079d2', tstzrange('2031-03-02 10:00+00', '2031-03-02 10:30+00'), 'confirmed');
insert into public.sesiones_web (tenant_id, end_customer_id, token_hash, expira_at) values
  ('00000000-0000-0000-0000-0000000079a1', '00000000-0000-0000-0000-0000000079c1', repeat('1', 64), now() + interval '1 day'),
  ('00000000-0000-0000-0000-0000000079b1', '00000000-0000-0000-0000-0000000079c2', repeat('2', 64), now() + interval '1 day');
insert into public.codigos_web (tenant_id, email_hash, codigo_hash, expira_at) values
  ('00000000-0000-0000-0000-0000000079a1', repeat('1', 64), repeat('3', 64), now() + interval '5 minutes'),
  ('00000000-0000-0000-0000-0000000079b1', repeat('2', 64), repeat('4', 64), now() + interval '5 minutes');
insert into public.tenant_integrations (id, tenant_id, integration_type, config) values
  ('00000000-0000-0000-0000-0000000079a9', '00000000-0000-0000-0000-0000000079a1', 'telegram_bot', '{}'),
  ('00000000-0000-0000-0000-0000000079b9', '00000000-0000-0000-0000-0000000079b1', 'telegram_bot', '{}');
-- Outbox: un aviso al cliente y uno al negocio por tenant
insert into public.notification_outbox (id, tenant_id, end_customer_id, destino, channel, template, params) values
  ('00000000-0000-0000-0000-000000790001', '00000000-0000-0000-0000-0000000079a1',
   '00000000-0000-0000-0000-0000000079c1', 'cliente', 'telegram', 'turno_confirmado',
   '{"booking_id": "00000000-0000-0000-0000-0000000079f1"}'),
  ('00000000-0000-0000-0000-000000790002', '00000000-0000-0000-0000-0000000079b1',
   '00000000-0000-0000-0000-0000000079c2', 'cliente', 'telegram', 'turno_confirmado',
   '{"booking_id": "00000000-0000-0000-0000-0000000079f2"}');
insert into public.notification_outbox (id, tenant_id, destino, template, params) values
  ('00000000-0000-0000-0000-000000790003', '00000000-0000-0000-0000-0000000079a1', 'negocio',
   'aviso_negocio_reserva', '{"booking_id": "00000000-0000-0000-0000-0000000079f1"}'),
  ('00000000-0000-0000-0000-000000790004', '00000000-0000-0000-0000-0000000079b1', 'negocio',
   'aviso_negocio_reserva', '{"booking_id": "00000000-0000-0000-0000-0000000079f2"}');

-- ---------------------------------------------------------------------------
-- Casos de solo lectura o sin efecto sobre los demás: cada uno acepta uno o más resultados
-- ---------------------------------------------------------------------------
create temp table casos (n serial, rol text, ctx text, sentencia text, esperado text[], descripcion text);

-- web_reservas: en cada tabla que tiene, con el contexto de A ve solo lo de A y sin contexto no ve nada
create temp table tablas_web (tabla text, col_tenant text);
insert into tablas_web values
  ('tenants', 'id'), ('services', 'tenant_id'), ('professionals', 'tenant_id'),
  ('professional_services', 'tenant_id'), ('availability_rules', 'tenant_id'), ('availability_blocks', 'tenant_id'),
  ('bookings', 'tenant_id'), ('end_customers', 'tenant_id'), ('canales_cliente', 'tenant_id'),
  ('booking_packs', 'tenant_id'), ('sesiones_web', 'tenant_id'), ('codigos_web', 'tenant_id');

insert into casos (rol, ctx, sentencia, esperado, descripcion)
select 'web_reservas', '00000000-0000-0000-0000-0000000079a1', format('select count(*) from public.%I', t.tabla),
       array['1'], 'web_reservas con el contexto de A ve una sola fila de ' || t.tabla
  from tablas_web t;
insert into casos (rol, ctx, sentencia, esperado, descripcion)
select 'web_reservas', '00000000-0000-0000-0000-0000000079a1',
       format('select count(*) from public.%I where %I = %L', t.tabla, t.col_tenant,
              '00000000-0000-0000-0000-0000000079b1'),
       array['0'], 'web_reservas con el contexto de A no ve filas de B en ' || t.tabla
  from tablas_web t;
insert into casos (rol, ctx, sentencia, esperado, descripcion)
select 'web_reservas', '', format('select count(*) from public.%I', t.tabla),
       array['0'], 'web_reservas sin contexto de tenant no ve ninguna fila de ' || t.tabla
  from tablas_web t;

-- web_reservas: escrituras sobre filas de B con el contexto de A (no actualizan nada)
insert into casos (rol, ctx, sentencia, esperado, descripcion) values
  ('web_reservas', '00000000-0000-0000-0000-0000000079a1',
   $q$update public.bookings set status = status where tenant_id = '00000000-0000-0000-0000-0000000079b1'$q$,
   array['ok 0'], 'web_reservas no actualiza bookings de B'),
  ('web_reservas', '00000000-0000-0000-0000-0000000079a1',
   $q$update public.end_customers set name = 'x' where tenant_id = '00000000-0000-0000-0000-0000000079b1'$q$,
   array['ok 0'], 'web_reservas no actualiza end_customers de B'),
  ('web_reservas', '00000000-0000-0000-0000-0000000079a1',
   $q$update public.canales_cliente set verificado_at = now() where tenant_id = '00000000-0000-0000-0000-0000000079b1'$q$,
   array['ok 0'], 'web_reservas no actualiza canales_cliente de B'),
  ('web_reservas', '00000000-0000-0000-0000-0000000079a1',
   $q$update public.booking_packs set used_sessions = used_sessions where tenant_id = '00000000-0000-0000-0000-0000000079b1'$q$,
   array['ok 0'], 'web_reservas no actualiza booking_packs de B'),
  ('web_reservas', '00000000-0000-0000-0000-0000000079a1',
   $q$update public.sesiones_web set revocada_at = now() where tenant_id = '00000000-0000-0000-0000-0000000079b1'$q$,
   array['ok 0'], 'web_reservas no revoca sesiones_web de B'),
  ('web_reservas', '00000000-0000-0000-0000-0000000079a1',
   $q$update public.codigos_web set intentos = 1 where tenant_id = '00000000-0000-0000-0000-0000000079b1'$q$,
   array['ok 0'], 'web_reservas no actualiza codigos_web de B');

-- web_reservas: inserciones de filas de B con el contexto de A, o sin contexto (la policy o el trigger las rechazan)
insert into casos (rol, ctx, sentencia, esperado, descripcion) values
  ('web_reservas', '00000000-0000-0000-0000-0000000079a1',
   $q$insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status)
      values ('00000000-0000-0000-0000-0000000079b1', '00000000-0000-0000-0000-0000000079c2',
              '00000000-0000-0000-0000-0000000079e2', '00000000-0000-0000-0000-0000000079d2',
              tstzrange('2031-03-03 10:00+00', '2031-03-03 10:30+00'), 'confirmed')$q$,
   array['err 42501', 'err 23503'], 'web_reservas no inserta un turno de B'),
  ('web_reservas', '00000000-0000-0000-0000-0000000079a1',
   $q$insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status)
      values ('00000000-0000-0000-0000-0000000079a1', '00000000-0000-0000-0000-0000000079c2',
              '00000000-0000-0000-0000-0000000079e1', '00000000-0000-0000-0000-0000000079d1',
              tstzrange('2031-03-03 11:00+00', '2031-03-03 11:30+00'), 'confirmed')$q$,
   array['err 23503', 'err 42501'], 'web_reservas no reserva un turno de A para un cliente de B'),
  ('web_reservas', '00000000-0000-0000-0000-0000000079a1',
   $q$insert into public.end_customers (tenant_id, name) values ('00000000-0000-0000-0000-0000000079b1', 'x')$q$,
   array['err 42501'], 'web_reservas no inserta una ficha de B'),
  ('web_reservas', '00000000-0000-0000-0000-0000000079a1',
   $q$insert into public.canales_cliente (tenant_id, end_customer_id, canal, identificador)
      values ('00000000-0000-0000-0000-0000000079b1', '00000000-0000-0000-0000-0000000079c2', 'email', 'z@b.test')$q$,
   array['err 42501'], 'web_reservas no inserta un canal de B'),
  ('web_reservas', '00000000-0000-0000-0000-0000000079a1',
   $q$insert into public.sesiones_web (tenant_id, end_customer_id, token_hash, expira_at)
      values ('00000000-0000-0000-0000-0000000079b1', '00000000-0000-0000-0000-0000000079c2', repeat('5', 64),
              now() + interval '1 day')$q$,
   array['err 42501'], 'web_reservas no inserta una sesión de B'),
  ('web_reservas', '00000000-0000-0000-0000-0000000079a1',
   $q$insert into public.codigos_web (tenant_id, email_hash, codigo_hash, expira_at)
      values ('00000000-0000-0000-0000-0000000079b1', repeat('5', 64), repeat('6', 64), now() + interval '5 minutes')$q$,
   array['err 42501'], 'web_reservas no inserta un código de B'),
  ('web_reservas', '',
   $q$insert into public.end_customers (tenant_id, name) values ('00000000-0000-0000-0000-0000000079a1', 'x')$q$,
   array['err 42501'], 'web_reservas sin contexto no inserta una ficha'),
  ('web_reservas', '',
   $q$insert into public.codigos_web (tenant_id, email_hash, codigo_hash, expira_at)
      values ('00000000-0000-0000-0000-0000000079a1', repeat('5', 64), repeat('6', 64), now() + interval '5 minutes')$q$,
   array['err 42501'], 'web_reservas sin contexto no inserta un código');

-- web_reservas: funciones
insert into casos (rol, ctx, sentencia, esperado, descripcion) values
  ('web_reservas', '', $q$select count(*) from public.resolver_slug_tenant('aislamiento-a')$q$,
   array['1'], 'web_reservas sin contexto resuelve el slug de un negocio activo'),
  ('web_reservas', '', $q$select count(*) from public.resolver_slug_tenant('no-existe')$q$,
   array['0'], 'web_reservas sin contexto no resuelve un slug inexistente'),
  ('web_reservas', '00000000-0000-0000-0000-0000000079a1',
   $q$select public.enqueue_notification('00000000-0000-0000-0000-0000000079c2', 'telegram', 'turno_confirmado')::text$q$,
   array['err 42501'], 'web_reservas no encola un aviso para un cliente de B'),
  ('web_reservas', '',
   $q$select public.enqueue_notification('00000000-0000-0000-0000-0000000079c1', 'telegram', 'turno_confirmado')::text$q$,
   array['err 42501'], 'web_reservas sin contexto no encola un aviso al cliente'),
  ('web_reservas', '',
   $q$select public.encolar_aviso_negocio('aviso_negocio_reserva', '{"booking_id": "00000000-0000-0000-0000-0000000079f1"}')::text$q$,
   array['err 42501'], 'web_reservas sin contexto no encola un aviso al negocio');

-- enviador: ve y completa solo lo del negocio de su contexto
insert into casos (rol, ctx, sentencia, esperado, descripcion) values
  ('enviador', '00000000-0000-0000-0000-0000000079a1',
   $q$select (count(*) > 0)::text from public.resolve_outbox_delivery('00000000-0000-0000-0000-000000790001')$q$,
   array['true'], 'enviador con el contexto de A resuelve un aviso de A'),
  ('enviador', '00000000-0000-0000-0000-0000000079a1',
   $q$select count(*) from public.resolve_outbox_delivery('00000000-0000-0000-0000-000000790002')$q$,
   array['0'], 'enviador con el contexto de A no resuelve un aviso del cliente de B'),
  ('enviador', '00000000-0000-0000-0000-0000000079a1',
   $q$select count(*) from public.resolve_outbox_delivery('00000000-0000-0000-0000-000000790004')$q$,
   array['0'], 'enviador con el contexto de A no resuelve un aviso al negocio de B'),
  ('enviador', '00000000-0000-0000-0000-0000000079a1',
   $q$select public.registrar_entrega_outbox('00000000-0000-0000-0000-000000790002', 'cc:x', 'sent')::text$q$,
   array['false'], 'enviador con el contexto de A no registra la entrega de un aviso de B'),
  ('enviador', '00000000-0000-0000-0000-0000000079a1',
   $q$select public.complete_notification_outbox('00000000-0000-0000-0000-000000790002', 'sent')::text$q$,
   array['false'], 'enviador con el contexto de A no completa un aviso de B'),
  ('enviador', '00000000-0000-0000-0000-0000000079a1',
   $q$select public.get_integration_secret('00000000-0000-0000-0000-0000000079b9')::text$q$,
   array['err 42501'], 'enviador con el contexto de A no lee el secreto de una integración de B'),
  ('enviador', '00000000-0000-0000-0000-0000000079a1',
   $q$select public.get_integration_secret('00000000-0000-0000-0000-0000000079a9')::text$q$,
   array['<null>'], 'enviador con el contexto de A lee el secreto de una integración de A (sin secreto cargado)'),
  ('enviador', '00000000-0000-0000-0000-0000000079a1',
   $q$select public.find_integration('telegram_bot')::text$q$,
   array['00000000-0000-0000-0000-0000000079a9'], 'enviador con el contexto de A encuentra solo la integración de A'),
  ('enviador', '00000000-0000-0000-0000-0000000079b1',
   $q$select public.find_integration('telegram_bot')::text$q$,
   array['00000000-0000-0000-0000-0000000079b9'], 'enviador con el contexto de B encuentra solo la integración de B'),
  ('enviador', '00000000-0000-0000-0000-0000000079a1',
   $q$select public.record_notification_attempt('00000000-0000-0000-0000-0000000079c2', 'telegram', 'turno_confirmado', 'sent')::text$q$,
   array['err 42501'], 'enviador con el contexto de A no registra un envío para un cliente de B'),
  ('enviador', '',
   $q$select count(*) from public.resolve_outbox_delivery('00000000-0000-0000-0000-000000790001')$q$,
   array['err 42501'], 'enviador sin contexto no resuelve ningún aviso'),
  ('enviador', '',
   $q$select public.registrar_entrega_outbox('00000000-0000-0000-0000-000000790001', 'cc:x', 'sent')::text$q$,
   array['err 42501'], 'enviador sin contexto no registra entregas'),
  ('enviador', '',
   $q$select public.complete_notification_outbox('00000000-0000-0000-0000-000000790001', 'sent')::text$q$,
   array['err 42501'], 'enviador sin contexto no completa avisos'),
  ('enviador', '',
   $q$select public.find_integration('telegram_bot')::text$q$,
   array['err 42501'], 'enviador sin contexto no encuentra integraciones'),
  ('enviador', '',
   $q$select public.get_integration_secret('00000000-0000-0000-0000-0000000079a9')::text$q$,
   array['err 42501'], 'enviador sin contexto no lee secretos'),
  ('enviador', '',
   $q$select public.record_notification_attempt('00000000-0000-0000-0000-0000000079c1', 'telegram', 'turno_confirmado', 'sent')::text$q$,
   array['err 42501'], 'enviador sin contexto no registra envíos');

select plan(19 + (select count(*)::int from casos));

select is(case when v.resultado = any (v.esperado) then 'esperado' else v.resultado end, 'esperado',
          v.descripcion || ' [' || v.rol || ']')
  from (select c.n, c.rol, c.esperado, c.descripcion, pg_temp.como(c.rol, c.ctx, c.sentencia) as resultado
          from casos c) v
 order by v.n;

-- ---------------------------------------------------------------------------
-- Con efectos: escrituras propias y verificación de que lo de B quedó intacto
-- ---------------------------------------------------------------------------
select is((select count(*) from public.sesiones_web where tenant_id = '00000000-0000-0000-0000-0000000079b1'
              and revocada_at is not null)
        + (select count(*) from public.codigos_web where tenant_id = '00000000-0000-0000-0000-0000000079b1'
              and intentos <> 0)
        + (select count(*) from public.end_customers where tenant_id = '00000000-0000-0000-0000-0000000079b1'
              and name <> 'Cliente B'),
  0::bigint, 'las escrituras de web_reservas con el contexto de A no tocaron sesiones, códigos ni fichas de B');  -- 1
select is((select count(*) from public.end_customers), 2::bigint,
  'ninguna inserción rechazada dejó fichas de más');  -- 2
select is((select count(*) from public.bookings), 2::bigint, 'ninguna inserción rechazada dejó turnos de más');  -- 3
select is((select status from public.notification_outbox where id = '00000000-0000-0000-0000-000000790002'), 'pending',
  'el aviso de B sigue pendiente tras los intentos del enviador con el contexto de A');  -- 4

-- web_reservas encola con el contexto de A: las filas son de A
select is(pg_temp.como('web_reservas', '00000000-0000-0000-0000-0000000079a1',
  $q$select (public.enqueue_notification('00000000-0000-0000-0000-0000000079c1', 'telegram', 'turno_confirmado',
             '{"booking_id": "00000000-0000-0000-0000-0000000079f1"}') is not null)::text$q$),
  'true', 'web_reservas encola un aviso al cliente de A');  -- 5
select is(pg_temp.como('web_reservas', '00000000-0000-0000-0000-0000000079a1',
  $q$select (public.encolar_aviso_negocio('aviso_negocio_reserva',
             '{"booking_id": "00000000-0000-0000-0000-0000000079f1"}') is not null)::text$q$),
  'true', 'web_reservas encola un aviso al negocio de A');  -- 6
select is((select count(*) from public.notification_outbox where tenant_id = '00000000-0000-0000-0000-0000000079a1'), 4::bigint,
  'los dos avisos encolados por la web quedaron en el negocio de A');  -- 7
select is((select count(*) from public.notification_outbox where tenant_id = '00000000-0000-0000-0000-0000000079b1'), 2::bigint,
  'y ninguno en el de B');  -- 8

-- enviador: completa y registra solo con el contexto del aviso
select is(pg_temp.como('enviador', '00000000-0000-0000-0000-0000000079a1',
  $q$select public.complete_notification_outbox('00000000-0000-0000-0000-000000790003', 'sent')::text$q$),
  'true', 'enviador con el contexto de A completa un aviso de A');  -- 9
select is((select status from public.notification_outbox where id = '00000000-0000-0000-0000-000000790003'), 'sent',
  'el aviso de A quedó enviado');  -- 10
select is(pg_temp.como('enviador', '00000000-0000-0000-0000-0000000079a1',
  $q$select (public.record_notification_attempt('00000000-0000-0000-0000-0000000079c1', 'telegram', 'turno_confirmado',
             'sent') is not null)::text$q$),
  'true', 'enviador con el contexto de A registra un envío a un cliente de A');  -- 11
select is((select count(*) from public.notifications_log where tenant_id = '00000000-0000-0000-0000-0000000079a1'
              and end_customer_id = '00000000-0000-0000-0000-0000000079c1'), 1::bigint,
  'el registro del envío lleva el negocio del contexto');  -- 12
select is((select count(*) from public.notifications_log where tenant_id = '00000000-0000-0000-0000-0000000079b1'), 0::bigint,
  'y ninguno quedó en B');  -- 13
-- claim_notification_outbox no recibe contexto: reclama filas de todos los negocios (por diseño)
select is(pg_temp.como('enviador', '',
  $q$select count(distinct c.tenant_id) from public.claim_notification_outbox(20, 300, 5) c$q$),
  '2', 'enviador reclama avisos pendientes de todos los negocios, sin contexto (por diseño)');  -- 14

-- Con los roles de la web y del enviador no hay forma de leer los datos de B sin cambiar el contexto: las tablas
-- que tiene la web son exactamente las 12 de la lista y el enviador no tiene ninguna
select is((select count(distinct c.relname) from pg_class c
            where c.relnamespace = 'public'::regnamespace and c.relkind in ('r', 'p', 'v', 'm', 'f')
              and has_any_column_privilege('web_reservas', c.oid, 'SELECT,INSERT,UPDATE')),
  (select count(*) from tablas_web), 'las tablas que tiene web_reservas son exactamente las 12 que se probaron');  -- 15
select is((select count(*) from pg_class c
            where c.relnamespace = 'public'::regnamespace and c.relkind in ('r', 'p', 'v', 'm', 'f')
              and has_any_column_privilege('enviador', c.oid, 'SELECT,INSERT,UPDATE')),
  0::bigint, 'enviador no tiene ninguna tabla');  -- 16
select is((select count(*) from pg_class c
            where c.relnamespace = 'public'::regnamespace and c.relkind = 'r' and not c.relrowsecurity
              and c.oid in (select ('public.' || t.tabla)::regclass from tablas_web t)),
  0::bigint, 'todas las tablas de web_reservas tienen RLS activada');  -- 17
select is((select count(*) from pg_policies p
            where p.schemaname = 'public' and p.policyname = 'tenant_isolation'
              and p.tablename in (select t.tabla from tablas_web t)),
  (select count(*) from tablas_web), 'cada tabla de web_reservas tiene su policy tenant_isolation');  -- 18
select is((select count(*) from pg_roles where rolname in ('web_reservas', 'enviador') and rolbypassrls), 0::bigint,
  'ninguno de los dos roles omite RLS');  -- 19

select * from finish();
rollback;
