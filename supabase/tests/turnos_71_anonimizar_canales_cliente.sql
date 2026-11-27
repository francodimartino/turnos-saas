-- La anonimización y la purga borran los canales del cliente: el identificador en claro (p. ej. el id de Telegram)
-- no sobrevive al derecho al olvido y la unicidad queda libre.
begin;
select plan(8);

do $$
begin
  delete from vault.secrets where name = 'channel_user_hmac_key';
  perform vault.create_secret('clave-de-prueba-hmac', 'channel_user_hmac_key');
end
$$;

insert into public.tenants (id, name, slug, status, deactivated_at) values
  ('00000000-0000-0000-0000-0000000000a1', 'A', 'anoncan-a', 'active', null),
  ('00000000-0000-0000-0000-0000000000a3', 'C', 'anoncan-c', 'inactive', now() - interval '100 days');
insert into public.end_customers (id, tenant_id, channel, channel_user_id, phone, name) values
  ('00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000a1', 'telegram', 'tg-1', '+5491100000001', 'X'),
  ('00000000-0000-0000-0000-0000000000c2', '00000000-0000-0000-0000-0000000000a1', 'telegram', 'tg-2', '+5491100000002', 'Y'),
  ('00000000-0000-0000-0000-0000000000c3', '00000000-0000-0000-0000-0000000000a3', 'telegram', 'tg-3', '+5491100000003', 'Z'),
  ('00000000-0000-0000-0000-0000000000c4', '00000000-0000-0000-0000-0000000000a3', 'telegram', 'tg-4', '+5491100000004', 'W');
insert into public.canales_cliente (tenant_id, end_customer_id, canal, identificador, verificado_at) values
  ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', 'telegram', 'tg-1', now()),
  ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1', 'email', 'x@mail.com', now()),
  ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c2', 'telegram', 'tg-2', now()),
  ('00000000-0000-0000-0000-0000000000a3', '00000000-0000-0000-0000-0000000000c3', 'telegram', 'tg-3', now()),
  ('00000000-0000-0000-0000-0000000000a3', '00000000-0000-0000-0000-0000000000c4', 'email', 'w@mail.com', now());

select public.anonymize_end_customer_core('00000000-0000-0000-0000-0000000000c1', 'system', null);

select is((select count(*) from public.canales_cliente where end_customer_id = '00000000-0000-0000-0000-0000000000c1'),  -- 1
  0::bigint, 'anonimizar borra todos los canales de la ficha');
select is((select count(*) from public.canales_cliente where end_customer_id = '00000000-0000-0000-0000-0000000000c2'),  -- 2
  1::bigint, 'los canales de otras fichas quedan intactos');
select is((select count(*) from public.canales_cliente where identificador in ('tg-1', 'x@mail.com')),  -- 3
  0::bigint, 'el identificador en claro ya no está en la base');
select lives_ok($$insert into public.canales_cliente (tenant_id, end_customer_id, canal, identificador)
  values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c2', 'telegram', 'tg-1')$$,  -- 4
  'la unicidad queda libre: el identificador puede volver a usarse');
select ok((select metadata -> 'fields_anonymized' from public.audit_log  -- 5
            where action = 'anonymize_end_customer' and entity_id = '00000000-0000-0000-0000-0000000000c1')
  @> '["canales_cliente"]'::jsonb, 'la constancia en audit_log nombra canales_cliente');

-- purga del tenant C: no queda ningún canal, ni el de una ficha ya anonimizada antes
select public.anonymize_end_customer_core('00000000-0000-0000-0000-0000000000c3', 'system', null);
insert into public.canales_cliente (tenant_id, end_customer_id, canal, identificador, verificado_at) values
  ('00000000-0000-0000-0000-0000000000a3', '00000000-0000-0000-0000-0000000000c3', 'email', 'resto@mail.com', now());
select is((select count(*) from public.canales_cliente where tenant_id = '00000000-0000-0000-0000-0000000000a3'),  -- 6
  2::bigint, 'antes de la purga hay canales (incluido uno colgado de una ficha anonimizada)');
select count(*) from public.purge_tenant_data('00000000-0000-0000-0000-0000000000a3');
select is((select count(*) from public.canales_cliente where tenant_id = '00000000-0000-0000-0000-0000000000a3'),  -- 7
  0::bigint, 'la purga del tenant no deja ningún canal');
select is((select count(*) from public.canales_cliente where tenant_id = '00000000-0000-0000-0000-0000000000a1'),  -- 8
  2::bigint, 'la purga no toca los canales de otro tenant');

select * from finish();
rollback;
