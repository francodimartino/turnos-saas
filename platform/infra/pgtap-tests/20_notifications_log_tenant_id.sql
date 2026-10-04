-- notifications_log: tenant_id lo fuerza un trigger desde end_customers y las FKs son compuestas
-- (nota de RLS de §7).
begin;
select plan(7);

insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000000a1', 'A', 'nl-a'),
  ('00000000-0000-0000-0000-0000000000a2', 'B', 'nl-b');
insert into public.end_customers (id, tenant_id, channel, channel_user_id) values
  ('00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000a1', 'telegram', 'u1'),
  ('00000000-0000-0000-0000-0000000000c2', '00000000-0000-0000-0000-0000000000a2', 'telegram', 'u2');

-- INSERT con un tenant_id deliberadamente distinto
select lives_ok(
  $$insert into public.notifications_log (id, tenant_id, end_customer_id, channel)
    values ('00000000-0000-0000-0000-0000000000d1', '00000000-0000-0000-0000-0000000000a2',
            '00000000-0000-0000-0000-0000000000c1', 'telegram')$$,
  'un INSERT con tenant_id de otro tenant no falla...');
select is(
  (select tenant_id from public.notifications_log where id = '00000000-0000-0000-0000-0000000000d1'),
  '00000000-0000-0000-0000-0000000000a1'::uuid,
  '...y el trigger lo corrige al tenant real del end_customer');

-- UPDATE con un tenant_id deliberadamente distinto
update public.notifications_log set tenant_id = '00000000-0000-0000-0000-0000000000a2'
 where id = '00000000-0000-0000-0000-0000000000d1';
select is(
  (select tenant_id from public.notifications_log where id = '00000000-0000-0000-0000-0000000000d1'),
  '00000000-0000-0000-0000-0000000000a1'::uuid,
  'un UPDATE de tenant_id queda corregido por el trigger');

select throws_ok(
  $$insert into public.notifications_log (tenant_id, end_customer_id, channel)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000f0', 'telegram')$$,
  '23503', null, 'un end_customer inexistente es rechazado');

-- FK compuesta: con el trigger apagado (que ya corrige el valor), una referencia cruzada
-- entre tenants queda físicamente rechazada por la FK (tenant_id, end_customer_id).
alter table public.notifications_log disable trigger user;
select throws_ok(
  $$insert into public.notifications_log (tenant_id, end_customer_id, channel)
    values ('00000000-0000-0000-0000-0000000000a2', '00000000-0000-0000-0000-0000000000c1', 'telegram')$$,
  '23503', null, 'la FK compuesta rechaza un end_customer de otro tenant');
alter table public.notifications_log enable trigger user;

select is(
  (select count(*) from pg_constraint
    where conrelid = 'public.notifications_log'::regclass and contype = 'f'
      and conname = 'notifications_log_tenant_end_customer_fkey'
      and array_length(conkey, 1) = 2),
  1::bigint, 'la FK hacia end_customers es compuesta (tenant_id, end_customer_id)');
select has_index('public', 'notifications_log', 'notifications_log_tenant_id_idx',
  'notifications_log tiene índice por tenant_id');

select * from finish();
rollback;
