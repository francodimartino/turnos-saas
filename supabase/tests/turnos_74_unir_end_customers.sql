-- unir_end_customers (§2.12): mueve turnos, packs, canales, avisos y registros de envío de la ficha absorbida a la
-- principal, completa los datos vacíos, anonimiza la absorbida y deja la constancia en audit_log. Se prueba con el
-- contexto de tenant de la transacción. La lógica está en unir_end_customers_core (el caller de pgTAP es el rol de
-- migraciones y `set role` no cambia session_user, así que el actor se pasa como lo derivaría la función pública);
-- la puerta pública con el session_user real y el actor tomado del contexto los cubren los tests de integración de
-- Python, que se conectan como dashboard_service.
begin;
select plan(45);

do $$
declare
  v_rol text;
begin
  foreach v_rol in array array['bot_service', 'dashboard_service'] loop
    execute format('grant %I to current_user with inherit false, set true', v_rol);
  end loop;
  delete from vault.secrets where name = 'channel_user_hmac_key';
  perform vault.create_secret('clave-de-prueba-hmac', 'channel_user_hmac_key');
end
$$;

-- Corre una sentencia con un rol real y el contexto de tenant y de actor; devuelve el resultado como texto o
-- 'err <sqlstate>'. Un error deshace lo que la sentencia hubiera escrito (subtransacción).
create function pg_temp.como(p_rol text, p_tenant uuid, p_sql text) returns text
language plpgsql as $$
declare
  v_res text;
begin
  perform set_config('app.current_tenant_id', coalesce(p_tenant::text, ''), true);
  perform set_config('app.current_actor_id', '00000000-0000-0000-0000-0000000074f1', true);
  if p_rol is not null then
    execute format('set role %I', p_rol);
  end if;
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

create function pg_temp.unir(p_tenant uuid, p_principal uuid, p_absorbida uuid) returns text
language sql as $$
  select pg_temp.como(null, p_tenant,
    format('select public.unir_end_customers_core(%L, %L, ''staff'', %L)::text', p_principal, p_absorbida,
           '00000000-0000-0000-0000-0000000074f1'::uuid))
$$;

insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000074a1', 'A', 'unir-a'),
  ('00000000-0000-0000-0000-0000000074a2', 'B', 'unir-b');
insert into public.professionals (id, tenant_id, name) values
  ('00000000-0000-0000-0000-0000000074b1', '00000000-0000-0000-0000-0000000074a1', 'P');
insert into public.services (id, tenant_id, name, duration_minutes, price) values
  ('00000000-0000-0000-0000-0000000074e1', '00000000-0000-0000-0000-0000000074a1', 'Corte', 30, 100);

-- Fichas del tenant A: 01 principal y 02 absorbida (caso completo); 03 y 07 con Telegram distinto (conflicto);
-- 05 y 06 (datos vacíos); 10 y 11 (uniones cruzadas); 12 y 13 (canal en las columnas viejas). 20 es del tenant B.
insert into public.end_customers (id, tenant_id, channel, channel_user_id, phone, name, email, extra_data) values
  ('00000000-0000-0000-0000-000000074001', '00000000-0000-0000-0000-0000000074a1', 'telegram', 'tg-1', '+5491100000001', 'Principal', 'p@mail.test', '{}'),
  ('00000000-0000-0000-0000-000000074002', '00000000-0000-0000-0000-0000000074a1', null, null, '+5491100000002', 'Absorbida', 'a@mail.test', '{}'),
  ('00000000-0000-0000-0000-000000074003', '00000000-0000-0000-0000-0000000074a1', 'telegram', 'tg-3', '+5491100000003', 'Tres', null, '{}'),
  ('00000000-0000-0000-0000-000000074005', '00000000-0000-0000-0000-0000000074a1', null, null, null, 'Cinco', null, '{"k": "p"}'),
  ('00000000-0000-0000-0000-000000074006', '00000000-0000-0000-0000-0000000074a1', null, null, '+5491100000006', 'Seis', 'e6@mail.test', '{"k": "a", "j": "a"}'),
  ('00000000-0000-0000-0000-000000074007', '00000000-0000-0000-0000-0000000074a1', 'telegram', 'tg-7', '+5491100000007', 'Siete', null, '{}'),
  ('00000000-0000-0000-0000-000000074010', '00000000-0000-0000-0000-0000000074a1', null, null, '+5491100000010', 'Diez', null, '{}'),
  ('00000000-0000-0000-0000-000000074011', '00000000-0000-0000-0000-0000000074a1', null, null, '+5491100000011', 'Once', null, '{}'),
  ('00000000-0000-0000-0000-000000074012', '00000000-0000-0000-0000-0000000074a1', 'telegram', 'tg-12', '+5491100000012', 'Doce', null, '{}'),
  ('00000000-0000-0000-0000-000000074013', '00000000-0000-0000-0000-0000000074a1', null, null, '+5491100000013', 'Trece', null, '{}'),
  ('00000000-0000-0000-0000-000000074020', '00000000-0000-0000-0000-0000000074a2', null, null, '+5491100000020', 'Otro negocio', 'b@mail.test', '{}');
insert into public.end_customers (id, tenant_id, channel, channel_user_id, phone, name, email) values
  ('00000000-0000-0000-0000-000000074030', '00000000-0000-0000-0000-0000000074a1', null, null, '+5491100000030', 'Treinta', null),
  ('00000000-0000-0000-0000-000000074031', '00000000-0000-0000-0000-0000000074a1', null, null, '+5491100000030', 'Treinta y uno', null),
  ('00000000-0000-0000-0000-000000074032', '00000000-0000-0000-0000-0000000074a1', null, null, null, 'Treinta y dos', null),
  ('00000000-0000-0000-0000-000000074033', '00000000-0000-0000-0000-0000000074a1', null, null, '+5491100000033', 'Treinta y tres', 'e33@mail.test'),
  ('00000000-0000-0000-0000-000000074034', '00000000-0000-0000-0000-0000000074a1', 'whatsapp', 'wa-34', '+5491100000034', 'Treinta y cuatro', null),
  ('00000000-0000-0000-0000-000000074035', '00000000-0000-0000-0000-0000000074a1', null, null, '+5491100000035', 'Treinta y cinco', null);
insert into public.notification_outbox (id, tenant_id, end_customer_id, channel, template) values
  ('00000000-0000-0000-0000-000000074201', '00000000-0000-0000-0000-0000000074a1', '00000000-0000-0000-0000-000000074035', 'telegram', 'recordatorio'),
  ('00000000-0000-0000-0000-000000074202', '00000000-0000-0000-0000-0000000074a1', '00000000-0000-0000-0000-000000074035', 'whatsapp', 'recordatorio');
insert into public.canales_cliente (tenant_id, end_customer_id, canal, identificador, verificado_at) values
  ('00000000-0000-0000-0000-0000000074a1', '00000000-0000-0000-0000-000000074001', 'telegram', 'tg-1', now()),
  ('00000000-0000-0000-0000-0000000074a1', '00000000-0000-0000-0000-000000074002', 'email', 'a@mail.test', now()),
  ('00000000-0000-0000-0000-0000000074a1', '00000000-0000-0000-0000-000000074002', 'whatsapp', 'wa-2', null),
  ('00000000-0000-0000-0000-0000000074a1', '00000000-0000-0000-0000-000000074003', 'telegram', 'tg-3', now()),
  ('00000000-0000-0000-0000-0000000074a1', '00000000-0000-0000-0000-000000074007', 'telegram', 'tg-7', now());

-- Turnos: uno de la principal; de la absorbida, tres confirmados/completados (uno con cambio de horario pendiente) y un
-- pendiente; uno de la ficha 03.
insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, status) values
  ('00000000-0000-0000-0000-000000740001', '00000000-0000-0000-0000-0000000074a1', '00000000-0000-0000-0000-000000074001', '00000000-0000-0000-0000-0000000074e1', '00000000-0000-0000-0000-0000000074b1', tstzrange('2030-11-01 10:00+00', '2030-11-01 10:30+00'), 'confirmed'),
  ('00000000-0000-0000-0000-000000740002', '00000000-0000-0000-0000-0000000074a1', '00000000-0000-0000-0000-000000074002', '00000000-0000-0000-0000-0000000074e1', '00000000-0000-0000-0000-0000000074b1', tstzrange('2030-11-02 10:00+00', '2030-11-02 10:30+00'), 'confirmed'),
  ('00000000-0000-0000-0000-000000740003', '00000000-0000-0000-0000-0000000074a1', '00000000-0000-0000-0000-000000074002', '00000000-0000-0000-0000-0000000074e1', '00000000-0000-0000-0000-0000000074b1', tstzrange('2030-11-03 10:00+00', '2030-11-03 10:30+00'), 'confirmed'),
  ('00000000-0000-0000-0000-000000740004', '00000000-0000-0000-0000-0000000074a1', '00000000-0000-0000-0000-000000074002', '00000000-0000-0000-0000-0000000074e1', '00000000-0000-0000-0000-0000000074b1', tstzrange('2030-11-04 10:00+00', '2030-11-04 10:30+00'), 'completed'),
  ('00000000-0000-0000-0000-000000740005', '00000000-0000-0000-0000-0000000074a1', '00000000-0000-0000-0000-000000074003', '00000000-0000-0000-0000-0000000074e1', '00000000-0000-0000-0000-0000000074b1', tstzrange('2030-11-05 10:00+00', '2030-11-05 10:30+00'), 'confirmed');
insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, status, pending_expires_at) values
  ('00000000-0000-0000-0000-000000740006', '00000000-0000-0000-0000-0000000074a1', '00000000-0000-0000-0000-000000074002', '00000000-0000-0000-0000-0000000074e1', '00000000-0000-0000-0000-0000000074b1', tstzrange('2030-11-06 10:00+00', '2030-11-06 10:30+00'), 'pending', '2030-11-06 08:00+00');
update public.bookings
   set starts_at_propuesto = '2030-11-02 14:00+00', ends_at_propuesto = '2030-11-02 14:30+00',
       cambio_pendiente_expira_at = now() + interval '1 day'
 where id = '00000000-0000-0000-0000-000000740002';
insert into public.booking_packs (id, tenant_id, end_customer_id, service_id, total_sessions, origin) values
  ('00000000-0000-0000-0000-000000074100', '00000000-0000-0000-0000-0000000074a1', '00000000-0000-0000-0000-000000074002', '00000000-0000-0000-0000-0000000074e1', 5, 'manual_grant');
insert into public.notification_outbox (id, tenant_id, end_customer_id, channel, template) values
  ('00000000-0000-0000-0000-000000074200', '00000000-0000-0000-0000-0000000074a1', '00000000-0000-0000-0000-000000074002', 'telegram', 'recordatorio');
insert into public.notifications_log (id, tenant_id, end_customer_id, channel, template, status) values
  ('00000000-0000-0000-0000-000000074300', '00000000-0000-0000-0000-0000000074a1', '00000000-0000-0000-0000-000000074002', 'telegram', 'recordatorio', 'sent');

-- Caso completo: 02 se absorbe en 01
create temp table res as
  select pg_temp.unir('00000000-0000-0000-0000-0000000074a1', '00000000-0000-0000-0000-000000074001', '00000000-0000-0000-0000-000000074002') as r;

select is((select (r::jsonb) ->> 'turnos' from res), '4', 'la unión devuelve cuántos turnos pasaron');  -- 1
select is((select ((r::jsonb) ->> 'packs') || '/' || ((r::jsonb) ->> 'canales') || '/' || ((r::jsonb) ->> 'avisos') || '/' || ((r::jsonb) ->> 'registros_envio') from res),  -- 2
  '1/2/1/1', 'devuelve los conteos de packs, canales, avisos y registros de envío');
select is((select count(*)::int from public.bookings where end_customer_id = '00000000-0000-0000-0000-000000074002'), 0,  -- 3
  'la absorbida se queda sin turnos');
select is((select count(*)::int from public.bookings where end_customer_id = '00000000-0000-0000-0000-000000074001'), 5,  -- 4
  'la principal tiene los turnos propios y los de la absorbida, de cualquier estado');
select is((select starts_at_propuesto is not null and time_range = tstzrange('2030-11-02 10:00+00', '2030-11-02 10:30+00')  -- 5
             from public.bookings where id = '00000000-0000-0000-0000-000000740002'), true,
  'el turno con cambio de horario pendiente pasa con su horario original y su propuesta intactos');
select is((select status from public.bookings where id = '00000000-0000-0000-0000-000000740006'), 'pending',  -- 6
  'el turno pendiente conserva su estado (la anonimización no lo cancela)');
select is((select end_customer_id from public.booking_packs where id = '00000000-0000-0000-0000-000000074100'),  -- 7
  '00000000-0000-0000-0000-000000074001'::uuid, 'el pack pasa a la principal');
select is((select end_customer_id || '|' || status from public.notification_outbox where id = '00000000-0000-0000-0000-000000074200'),  -- 8
  '00000000-0000-0000-0000-000000074001|pending', 'el aviso pendiente del outbox pasa a la principal y sigue pendiente');
select is((select end_customer_id from public.notifications_log where id = '00000000-0000-0000-0000-000000074300'),  -- 9
  '00000000-0000-0000-0000-000000074001'::uuid, 'el registro de envío pasa a la principal');
select is((select count(*)::int from public.canales_cliente where end_customer_id = '00000000-0000-0000-0000-000000074001'), 3,  -- 10
  'la principal suma los canales de la absorbida');
select is((select count(*)::int from public.canales_cliente where end_customer_id = '00000000-0000-0000-0000-000000074002'), 0,  -- 11
  'la absorbida no conserva ningún canal');
select is((select email || '|' || phone || '|' || name from public.end_customers where id = '00000000-0000-0000-0000-000000074001'),  -- 12
  'p@mail.test|+5491100000001|Principal', 'la principal conserva su email, teléfono y nombre');
select is((select deleted_at is not null and phone is null and name is null and email is null  -- 13
             from public.end_customers where id = '00000000-0000-0000-0000-000000074002'), true,
  'la absorbida queda anonimizada y sin email');
select is((select count(*)::int from public.audit_log  -- 14
            where action = 'anonymize_end_customer' and entity_id = '00000000-0000-0000-0000-000000074002'), 1,
  'la anonimización de la absorbida queda en audit_log');
select is((select actor_type || '|' || actor_id || '|' || tenant_id || '|' || entity_id || '|' || (metadata ->> 'absorbida_id')  -- 15
                  || '|' || (metadata ->> 'email_descartado') || '|' || (metadata ->> 'telefono_descartado')
                  || '|' || (metadata ->> 'turnos')
             from public.audit_log where action = 'customers_merged'),
  'staff|00000000-0000-0000-0000-0000000074f1|00000000-0000-0000-0000-0000000074a1|00000000-0000-0000-0000-000000074001|00000000-0000-0000-0000-000000074002|true|true|4',
  'audit_log guarda el actor del contexto, el tenant, ambos ids y los conteos');
select is((select metadata::text !~ '(@|\+54)' from public.audit_log where action = 'customers_merged'), true,  -- 16
  'la constancia no lleva email ni teléfono');

-- Datos vacíos de la principal: 06 se absorbe en 05
select pg_temp.unir('00000000-0000-0000-0000-0000000074a1', '00000000-0000-0000-0000-000000074005', '00000000-0000-0000-0000-000000074006');
select is((select email || '|' || phone || '|' || name || '|' || extra_data::text  -- 17
             from public.end_customers where id = '00000000-0000-0000-0000-000000074005'),
  'e6@mail.test|+5491100000006|Cinco|{"j": "a", "k": "p"}',
  'una principal sin email ni teléfono toma los de la absorbida, y gana en extra_data');
select is((select count(*)::int from public.end_customers where lower(email) = 'e6@mail.test'), 1,  -- 18
  'el email no queda repetido entre las dos fichas');

-- Canal en las columnas viejas (channel, channel_user_id) de la absorbida: 12 se absorbe en 13
select pg_temp.unir('00000000-0000-0000-0000-0000000074a1', '00000000-0000-0000-0000-000000074013', '00000000-0000-0000-0000-000000074012');
select is((select canal || ':' || identificador from public.canales_cliente  -- 19
            where end_customer_id = '00000000-0000-0000-0000-000000074013'), 'telegram:tg-12',
  'el canal de las columnas viejas de la absorbida pasa a canales_cliente de la principal');
select is((select channel_user_id is null and channel_user_hash is not null  -- 20
             from public.end_customers where id = '00000000-0000-0000-0000-000000074012'), true,
  'la absorbida queda con el hash y sin el identificador en claro');

select is((select channel || ':' || channel_user_id from public.end_customers  -- 20b
            where id = '00000000-0000-0000-0000-000000074013'), 'telegram:tg-12',
  'la principal sin canal de chat refleja el que recibió, para lo que todavía lee las columnas viejas');

-- Marcas de datos descartados: solo cuando los dos tienen el dato y es distinto
select pg_temp.unir('00000000-0000-0000-0000-0000000074a1', '00000000-0000-0000-0000-000000074030', '00000000-0000-0000-0000-000000074031');
select is((select (metadata ->> 'telefono_descartado') || '|' || (metadata ->> 'email_descartado')  -- 20c
             from public.audit_log where action = 'customers_merged' and entity_id = '00000000-0000-0000-0000-000000074030'), 'false|false',
  'el mismo teléfono en las dos fichas no cuenta como descartado');
select pg_temp.unir('00000000-0000-0000-0000-0000000074a1', '00000000-0000-0000-0000-000000074032', '00000000-0000-0000-0000-000000074033');
select is((select (metadata ->> 'telefono_descartado') || '|' || (metadata ->> 'email_descartado')  -- 20d
             from public.audit_log where action = 'customers_merged' and entity_id = '00000000-0000-0000-0000-000000074032'), 'false|false',
  'si la principal no tenía el dato, no hay nada descartado');

-- Avisos pendientes movidos: el de otro canal que el de la principal no se envía
select pg_temp.unir('00000000-0000-0000-0000-0000000074a1', '00000000-0000-0000-0000-000000074034', '00000000-0000-0000-0000-000000074035');
select is((select status || ':' || last_error from public.notification_outbox  -- 20e
            where id = '00000000-0000-0000-0000-000000074201'), 'failed:canal_sin_identificador_en_la_ficha_unida',
  'el aviso de Telegram movido a una principal sin Telegram queda failed con su motivo');
select is((select status from public.notification_outbox where id = '00000000-0000-0000-0000-000000074202'), 'pending',  -- 20f
  'el aviso del canal que sí tiene la principal sigue pendiente');

-- Bordes: cada uno con su error propio y sin tocar nada
select is(pg_temp.unir('00000000-0000-0000-0000-0000000074a1', '00000000-0000-0000-0000-000000074010', '00000000-0000-0000-0000-000000074010'),  -- 21
  'err UF004', 'una ficha no se une consigo misma');
select is(pg_temp.unir('00000000-0000-0000-0000-0000000074a1', '00000000-0000-0000-0000-000000074001', '00000000-0000-0000-0000-000000074002'),  -- 22
  'err UF002', 'una absorbida ya anonimizada se rechaza');
select is(pg_temp.unir('00000000-0000-0000-0000-0000000074a1', '00000000-0000-0000-0000-000000074002', '00000000-0000-0000-0000-000000074001'),  -- 23
  'err UF002', 'una principal ya anonimizada se rechaza');
select is(pg_temp.unir('00000000-0000-0000-0000-0000000074a1', '00000000-0000-0000-0000-000000074001', '00000000-0000-0000-0000-000000074020'),  -- 24
  'err UF001', 'una absorbida de otro tenant es indistinguible de una inexistente');
select is(pg_temp.unir('00000000-0000-0000-0000-0000000074a1', '00000000-0000-0000-0000-000000074020', '00000000-0000-0000-0000-000000074001'),  -- 25
  'err UF001', 'una principal de otro tenant es indistinguible de una inexistente');
select is(pg_temp.unir('00000000-0000-0000-0000-0000000074a1', '00000000-0000-0000-0000-000000074001', '00000000-0000-0000-0000-0000000074ff'),  -- 26
  'err UF001', 'una ficha inexistente da el mismo error');
select is(pg_temp.unir('00000000-0000-0000-0000-0000000074a2', '00000000-0000-0000-0000-000000074020', '00000000-0000-0000-0000-000000074010'),  -- 27
  'err UF001', 'desde el tenant B tampoco se ven las fichas de A');
select is(pg_temp.unir(null, '00000000-0000-0000-0000-000000074010', '00000000-0000-0000-0000-000000074011'),  -- 28
  'err 42501', 'sin contexto de tenant se rechaza');
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000074a1',  -- 29
    $q$select public.unir_end_customers('00000000-0000-0000-0000-000000074010', '00000000-0000-0000-0000-000000074011')::text$q$),
  'err 42501', 'un rol sin permiso sobre la función no puede unir');
select is(pg_temp.como('dashboard_service', '00000000-0000-0000-0000-0000000074a1',  -- 29b
    $q$select public.unir_end_customers_core('00000000-0000-0000-0000-000000074010', '00000000-0000-0000-0000-000000074011', 'staff', null)::text$q$),
  'err 42501', 'la función interna no la ejecuta ningún rol de aplicación');
select is(pg_temp.como(null, '00000000-0000-0000-0000-0000000074a1',  -- 29c
    $q$select public.unir_end_customers_core('00000000-0000-0000-0000-000000074010', '00000000-0000-0000-0000-000000074011', null, null)::text$q$),
  'err 42501', 'sin actor autorizado la unión se rechaza');
select is(pg_temp.unir('00000000-0000-0000-0000-0000000074a1', '00000000-0000-0000-0000-000000074007', '00000000-0000-0000-0000-000000074003'),  -- 30
  'err UF003', 'el mismo tipo de canal con identificadores distintos es un conflicto');
select is((select count(*)::int from public.bookings where end_customer_id = '00000000-0000-0000-0000-000000074003'), 1,  -- 31
  'el conflicto no movió nada: el turno sigue en su ficha');
select is((select deleted_at is null from public.end_customers where id = '00000000-0000-0000-0000-000000074003'), true,  -- 32
  'el conflicto no anonimizó la absorbida');
select is((select deleted_at is null and email = 'b@mail.test' from public.end_customers where id = '00000000-0000-0000-0000-000000074020'), true,  -- 33
  'la ficha de otro tenant quedó intacta');

-- Uniones cruzadas: la segunda encuentra la ficha ya anonimizada
select is(pg_temp.unir('00000000-0000-0000-0000-0000000074a1', '00000000-0000-0000-0000-000000074010', '00000000-0000-0000-0000-000000074011') like '{%', true,  -- 34
  'primera unión de la pareja');
select is(pg_temp.unir('00000000-0000-0000-0000-0000000074a1', '00000000-0000-0000-0000-000000074011', '00000000-0000-0000-0000-000000074010'),  -- 35
  'err UF002', 'la unión cruzada posterior se rechaza sin corromper');
select is((select phone from public.end_customers where id = '00000000-0000-0000-0000-000000074010'), '+5491100000010',  -- 36
  'la principal no se corrompió');
select is((select count(*)::int from public.audit_log where action = 'customers_merged'), 7,  -- 37
  'cada unión exitosa dejó una sola constancia');
select is((select count(*)::int from pg_proc p join pg_namespace n on n.oid = p.pronamespace  -- 38
            where n.nspname = 'public' and p.proname = 'unir_end_customers' and p.prosecdef
              and has_function_privilege('dashboard_service', p.oid, 'execute')
              and not has_function_privilege('bot_service', p.oid, 'execute')
              and not has_function_privilege('authenticated', p.oid, 'execute')), 1,
  'solo dashboard_service puede ejecutarla');

select * from finish();
rollback;
