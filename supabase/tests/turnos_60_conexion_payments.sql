-- Conexión turnos <-> payments (§2.10, §4.2): el trigger de payments resuelve el tenant_id real
-- desde bookings (payable_type 'booking_deposit') o booking_packs ('booking_pack'), tanto en INSERT
-- como en UPDATE. Un tenant_id deliberadamente distinto al del padre se corrige al valor real; un
-- payable_id inexistente se rechaza; un pago nunca se mueve de tenant.
begin;
select plan(14);

insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000000a1', 'A', 'conpay-a'),
  ('00000000-0000-0000-0000-0000000000a2', 'B', 'conpay-b');
insert into public.professionals (id, tenant_id, name) values
  ('00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-0000000000a1', 'Prof A'),
  ('00000000-0000-0000-0000-0000000000b2', '00000000-0000-0000-0000-0000000000a2', 'Prof B');
insert into public.services (id, tenant_id, name, duration_minutes, price) values
  ('00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000a1', 'Corte A', 30, 100),
  ('00000000-0000-0000-0000-0000000000e2', '00000000-0000-0000-0000-0000000000a2', 'Corte B', 30, 100);
insert into public.end_customers (id, tenant_id, channel, channel_user_id) values
  ('00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000a1', 'telegram', 'cp-a'),
  ('00000000-0000-0000-0000-0000000000c2', '00000000-0000-0000-0000-0000000000a2', 'telegram', 'cp-b');
insert into public.booking_packs (id, tenant_id, end_customer_id, service_id, total_sessions, origin) values
  ('00000000-0000-0000-0000-000000000401', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', 5, 'manual_grant'),
  ('00000000-0000-0000-0000-000000000402', '00000000-0000-0000-0000-0000000000a2', '00000000-0000-0000-0000-0000000000c2', '00000000-0000-0000-0000-0000000000e2', 5, 'manual_grant');
insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, status) values
  ('00000000-0000-0000-0000-000000000301', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1', tstzrange('2030-09-01 10:00+00', '2030-09-01 10:30+00'), 'confirmed'),
  ('00000000-0000-0000-0000-000000000302', '00000000-0000-0000-0000-0000000000a2', '00000000-0000-0000-0000-0000000000c2', '00000000-0000-0000-0000-0000000000e2', '00000000-0000-0000-0000-0000000000b2', tstzrange('2030-09-01 10:00+00', '2030-09-01 10:30+00'), 'confirmed'),
  ('00000000-0000-0000-0000-000000000303', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1', tstzrange('2030-09-02 10:00+00', '2030-09-02 10:30+00'), 'confirmed');

-- INSERT: rama booking_deposit
select lives_ok(  -- 1
  $$insert into public.payments (id, tenant_id, payable_type, payable_id, amount, status)
    values ('00000000-0000-0000-0000-000000000501', '00000000-0000-0000-0000-0000000000a1', 'booking_deposit',
            '00000000-0000-0000-0000-000000000301', 10, 'pending')$$,
  'un pago cuyo tenant_id coincide con el de su booking se inserta');
select lives_ok(  -- 2
  $$insert into public.payments (id, tenant_id, payable_type, payable_id, amount, status)
    values ('00000000-0000-0000-0000-000000000502', '00000000-0000-0000-0000-0000000000a1', 'booking_deposit',
            '00000000-0000-0000-0000-000000000302', 10, 'pending')$$,
  'un tenant_id distinto al del booking no hace fallar el INSERT');
select is((select tenant_id from public.payments where id = '00000000-0000-0000-0000-000000000502'),  -- 3
  '00000000-0000-0000-0000-0000000000a2'::uuid,
  'booking_deposit: el trigger corrige tenant_id al del booking (tenant B), no confía en el recibido (A)');

-- INSERT: rama booking_pack
select lives_ok(  -- 4
  $$insert into public.payments (id, tenant_id, payable_type, payable_id, amount, status)
    values ('00000000-0000-0000-0000-000000000503', '00000000-0000-0000-0000-0000000000a2', 'booking_pack',
            '00000000-0000-0000-0000-000000000401', 50, 'pending')$$,
  'pack: un tenant_id distinto al del pack tampoco hace fallar el INSERT');
select is((select tenant_id from public.payments where id = '00000000-0000-0000-0000-000000000503'),  -- 5
  '00000000-0000-0000-0000-0000000000a1'::uuid,
  'booking_pack: el trigger corrige tenant_id al del pack (tenant A), no confía en el recibido (B)');

-- payable_id que no existe en la tabla de su rama: se rechaza
select throws_ok(  -- 6
  $$insert into public.payments (tenant_id, payable_type, payable_id, amount, status)
    values ('00000000-0000-0000-0000-0000000000a1', 'booking_deposit',
            '00000000-0000-0000-0000-00000000dead', 1, 'pending')$$,
  '23503', null, 'booking_deposit con un payable_id inexistente se rechaza');
select throws_ok(  -- 7
  $$insert into public.payments (tenant_id, payable_type, payable_id, amount, status)
    values ('00000000-0000-0000-0000-0000000000a1', 'booking_pack',
            '00000000-0000-0000-0000-00000000dead', 1, 'pending')$$,
  '23503', null, 'booking_pack con un payable_id inexistente se rechaza');
select throws_ok(  -- 8
  $$insert into public.payments (tenant_id, payable_type, payable_id, amount, status)
    values ('00000000-0000-0000-0000-0000000000a1', 'booking_pack',
            '00000000-0000-0000-0000-000000000301', 1, 'pending')$$,
  '23503', null, 'cada rama busca solo en su tabla: el id de un booking no vale como pack');

-- sin payable_id no hay padre del que derivar: se respeta el tenant_id recibido (not null)
select lives_ok(  -- 9
  $$insert into public.payments (id, tenant_id, payable_type, amount, status)
    values ('00000000-0000-0000-0000-000000000504', '00000000-0000-0000-0000-0000000000a1', 'booking_deposit', 1, 'pending')$$,
  'sin payable_id el pago se inserta con el tenant_id recibido');

-- UPDATE: un pago nunca se mueve de tenant ni se re-apunta a un padre de otro tenant
select throws_ok(  -- 10
  $$update public.payments set tenant_id = '00000000-0000-0000-0000-0000000000a2'
     where id = '00000000-0000-0000-0000-000000000501'$$,
  '23514', null, 'UPDATE de tenant_id se rechaza (también con padre presente)');
select throws_ok(  -- 11
  $$update public.payments set payable_id = '00000000-0000-0000-0000-000000000302'
     where id = '00000000-0000-0000-0000-000000000501'$$,
  '23514', null, 'UPDATE de payable_id hacia un booking de otro tenant se rechaza');
select throws_ok(  -- 12
  $$update public.payments set payable_id = '00000000-0000-0000-0000-000000000302'
     where id = '00000000-0000-0000-0000-000000000504'$$,
  '23514', null, 'asignar a un pago sin padre un payable_id de otro tenant se rechaza');
select lives_ok(  -- 13
  $$update public.payments set payable_id = '00000000-0000-0000-0000-000000000303', status = 'approved'
     where id = '00000000-0000-0000-0000-000000000501'$$,
  'UPDATE hacia otro booking del mismo tenant y cambio de estado: permitido');
select is((select tenant_id from public.payments where id = '00000000-0000-0000-0000-000000000501'),  -- 14
  '00000000-0000-0000-0000-0000000000a1'::uuid, 'el tenant_id del pago no cambió');

select * from finish();
rollback;
