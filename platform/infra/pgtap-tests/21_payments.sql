-- payments: genérica, sin ramas de dominio en su trigger (§2.10, §4.2).
begin;
select plan(8);

insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000000a1', 'A', 'pay-a'),
  ('00000000-0000-0000-0000-0000000000a2', 'B', 'pay-b');

select lives_ok(
  $$insert into public.payments (tenant_id, payable_type, provider_payment_id, amount, status)
    values ('00000000-0000-0000-0000-0000000000a1', 'booking_deposit', 'mp-1', 100.50, 'pending')$$,
  'un pago válido se inserta');
select throws_ok(
  $$insert into public.payments (tenant_id, payable_type, provider_payment_id, amount, status)
    values ('00000000-0000-0000-0000-0000000000a2', 'booking_deposit', 'mp-1', 100, 'pending')$$,
  '23505', null, 'unique (provider, provider_payment_id): un webhook reintentado no se procesa dos veces');
select lives_ok(
  $$insert into public.payments (tenant_id, payable_type, amount, status)
    values ('00000000-0000-0000-0000-0000000000a1', 'booking_deposit', 1, 'pending'),
           ('00000000-0000-0000-0000-0000000000a1', 'booking_deposit', 1, 'pending')$$,
  'varios pagos sin provider_payment_id todavía no chocan');
select throws_ok(
  $$insert into public.payments (tenant_id, payable_type, amount, status)
    values (null, 'booking_deposit', 1, 'pending')$$,
  '23502', null, 'tenant_id no puede ser NULL (sin ramas de dominio no hay de dónde derivarlo)');
select throws_ok(
  $$insert into public.payments (tenant_id, payable_type, amount, status)
    values ('00000000-0000-0000-0000-0000000000f0', 'booking_deposit', 1, 'pending')$$,
  '23503', null, 'tenant_id debe existir');
select throws_ok(
  $$update public.payments set tenant_id = '00000000-0000-0000-0000-0000000000a2'
     where provider_payment_id = 'mp-1'$$,
  '23514', null, 'el trigger impide mover un pago a otro tenant');
select lives_ok(
  $$update public.payments set status = 'approved' where provider_payment_id = 'mp-1'$$,
  'el estado del pago se puede actualizar');
select is(
  (select count(*) from pg_trigger
    where tgrelid = 'public.payments'::regclass and tgname = 'payments_forzar_tenant_id'),
  1::bigint, 'payments tiene su trigger de tenant_id');

select * from finish();
rollback;
