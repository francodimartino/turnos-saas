-- Indice del listado de turnos por rango: bookings_tenant_time_range_idx (GiST tenant_id, time_range).
-- Se cargan muchos turnos de un tenant y de otro, se analiza la tabla y, con enable_seqscan = off (solo en la
-- transaccion), se verifica con EXPLAIN que la consulta REAL del listado (SQL_LISTAR_BOOKINGS, copiada tal cual
-- en el PREPARE) usa el indice nuevo y no recorre los turnos del tenant por bookings_tenant_id_idx.
begin;
select plan(7);

select has_index('public', 'bookings', 'bookings_tenant_time_range_idx', 'existe el indice del listado');
select is(
  (select am.amname from pg_class c join pg_am am on am.oid = c.relam
    where c.oid = 'public.bookings_tenant_time_range_idx'::regclass),
  'gist', 'el indice es GiST');
select is(
  (pg_get_indexdef('public.bookings_tenant_time_range_idx'::regclass) ~ '\(tenant_id, time_range\)'),
  true, 'el indice cubre (tenant_id, time_range)');

insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000000a1', 'A', 'lst-a'),
  ('00000000-0000-0000-0000-0000000000a2', 'B', 'lst-b');
insert into public.professionals (id, tenant_id, name) values
  ('00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-0000000000a1', 'Prof A'),
  ('00000000-0000-0000-0000-0000000000b2', '00000000-0000-0000-0000-0000000000a2', 'Prof B');
insert into public.services (id, tenant_id, name, duration_minutes, price) values
  ('00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000a1', 'Corte', 30, 100),
  ('00000000-0000-0000-0000-0000000000e2', '00000000-0000-0000-0000-0000000000a2', 'Corte', 30, 100);
insert into public.end_customers (id, tenant_id, channel, channel_user_id) values
  ('00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-0000000000a1', 'telegram', 'u1'),
  ('00000000-0000-0000-0000-0000000000c2', '00000000-0000-0000-0000-0000000000a2', 'telegram', 'u2');

-- 4000 turnos de media hora seguidos por tenant (unos 83 dias); el listado pide un solo dia
insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status)
  select '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000c1',
         '00000000-0000-0000-0000-0000000000e1', '00000000-0000-0000-0000-0000000000b1',
         tstzrange('2031-01-01 00:00+00'::timestamptz + (n * interval '30 minutes'),
                   '2031-01-01 00:30+00'::timestamptz + (n * interval '30 minutes')), 'confirmed'
    from generate_series(0, 3999) n;
insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status)
  select '00000000-0000-0000-0000-0000000000a2', '00000000-0000-0000-0000-0000000000c2',
         '00000000-0000-0000-0000-0000000000e2', '00000000-0000-0000-0000-0000000000b2',
         tstzrange('2031-01-01 00:00+00'::timestamptz + (n * interval '30 minutes'),
                   '2031-01-01 00:30+00'::timestamptz + (n * interval '30 minutes')), 'confirmed'
    from generate_series(0, 3999) n;

analyze public.bookings;
set local enable_seqscan = off;

-- Misma consulta que apps/shared/turnos_queries/consultas.py (SQL_LISTAR_BOOKINGS)
prepare listado(uuid, timestamptz, timestamptz, text[], uuid, int, int) as
select b.id, b.tenant_id, b.end_customer_id, b.service_id, b.professional_id,
       lower(b.time_range) as start_at, upper(b.time_range) as end_at,
       b.status, b.deposit_status, b.booking_pack_id, b.pending_expires_at, b.created_at,
       ec.name as customer_name, s.name as service_name, p.name as professional_name
from public.bookings b
join public.end_customers ec on ec.id = b.end_customer_id and ec.tenant_id = $1
join public.services s on s.id = b.service_id and s.tenant_id = $1
join public.professionals p on p.id = b.professional_id and p.tenant_id = $1
where b.tenant_id = $1
  and b.time_range && tstzrange($2::timestamptz, $3::timestamptz)
  and ($4::text[] is null or b.status = any($4::text[]))
  and ($5::uuid is null or b.professional_id = $5::uuid)
order by lower(b.time_range), b.id
limit $6 offset $7;

create function pg_temp.plan_listado() returns text
language plpgsql as $f$
declare r text; salida text := '';
begin
  for r in execute $q$explain execute listado('00000000-0000-0000-0000-0000000000a1',
      '2031-01-20 00:00+00', '2031-01-21 00:00+00', null, null, 200, 0)$q$ loop
    salida := salida || r || E'\n';
  end loop;
  return salida;
end
$f$;

select matches(pg_temp.plan_listado(), 'bookings_tenant_time_range_idx',
  'el listado por rango usa el indice (tenant_id, time_range)');
select doesnt_match(pg_temp.plan_listado(), 'bookings_tenant_id_idx',
  'el listado no recorre los turnos del tenant por bookings_tenant_id_idx');
select doesnt_match(pg_temp.plan_listado(), 'Seq Scan on bookings',
  'el listado no hace Seq Scan sobre bookings');
select is(
  (select count(*) from public.bookings
    where tenant_id = '00000000-0000-0000-0000-0000000000a1'
      and time_range && tstzrange('2031-01-20 00:00+00', '2031-01-21 00:00+00')),
  48::bigint, 'el filtro devuelve los 48 turnos del dia pedido');

select * from finish();
rollback;
