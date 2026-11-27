-- Canales del cliente: aislamiento por tenant, unicidad, copia desde end_customers, email y grants exactos.
begin;
select plan(27);

do $$
declare
  v_rol text;
begin
  foreach v_rol in array array['bot_service', 'dashboard_service', 'platform_admin_readonly'] loop
    execute format('grant %I to current_user with inherit false, set true', v_rol);
  end loop;
end
$$;

-- Ejecuta p_sql (un solo texto) como p_rol con el tenant p_tenant (puede ser null). Devuelve el texto o 'err <sqlstate>'.
create function pg_temp.como(p_rol text, p_tenant text, p_sql text) returns text
language plpgsql as $$
declare
  v_res text;
begin
  perform set_config('app.current_tenant_id', coalesce(p_tenant, ''), true);
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

insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000000a1', 'A', 'canales-a'),
  ('00000000-0000-0000-0000-0000000000b1', 'B', 'canales-b');

-- Datos previos: una ficha de Telegram, una anonimizada (sin identificador en claro) y una alta manual en el
-- tenant A; la misma persona de Telegram en el tenant B.
insert into public.end_customers (id, tenant_id, channel, channel_user_id, channel_user_hash, name, created_at) values
  ('00000000-0000-0000-0000-00000000c001', '00000000-0000-0000-0000-0000000000a1', 'telegram', '555', null, 'Ana',
   '2026-01-05 10:00+00'),
  ('00000000-0000-0000-0000-00000000c002', '00000000-0000-0000-0000-0000000000a1', 'telegram', null, 'hash-anonimo', null,
   '2026-01-06 10:00+00'),
  ('00000000-0000-0000-0000-00000000c003', '00000000-0000-0000-0000-0000000000a1', null, null, null, 'Alta manual',
   '2026-01-07 10:00+00'),
  ('00000000-0000-0000-0000-00000000c004', '00000000-0000-0000-0000-0000000000b1', 'telegram', '555', null, 'Ana en B',
   '2026-01-08 10:00+00');

-- La copia de la migración, sobre datos previos (se vacía la tabla para simular el estado anterior).
delete from public.canales_cliente;
create function pg_temp.copiar() returns void language sql as $$
  insert into public.canales_cliente (tenant_id, end_customer_id, canal, identificador, verificado_at, created_at)
  select tenant_id, id, channel, channel_user_id, created_at, created_at
    from public.end_customers
   where channel is not null and channel_user_id is not null
  on conflict (tenant_id, canal, identificador) do nothing
$$;
select pg_temp.copiar();

select is((select count(*) from public.canales_cliente), 2::bigint,  -- 1
  'se copian solo las fichas con identificador en claro (la anonimizada y la manual no)');
select is(
  (select count(*) from public.canales_cliente cc
     join public.end_customers ec on ec.tenant_id = cc.tenant_id and ec.id = cc.end_customer_id
    where cc.canal = ec.channel and cc.identificador = ec.channel_user_id
      and cc.verificado_at = ec.created_at and cc.created_at = ec.created_at),
  2::bigint, 'la copia es idéntica: canal, identificador, ficha y fecha de verificación');  -- 2
select pg_temp.copiar();
select is((select count(*) from public.canales_cliente), 2::bigint, 're-ejecutar la copia no duplica');  -- 3
select is((select count(*) from public.end_customers where channel is not null), 3::bigint,  -- 4
  'las columnas channel y channel_user_id siguen intactas');

-- Unicidad por tenant: la misma persona en dos tenants son dos filas; repetida en el mismo, error.
select is((select count(*) from public.canales_cliente where canal = 'telegram' and identificador = '555'), 2::bigint,  -- 5
  'el mismo identificador en dos tenants son dos filas');
select is(pg_temp.como('dashboard_service', '00000000-0000-0000-0000-0000000000a1',
  $$insert into public.canales_cliente (tenant_id, end_customer_id, canal, identificador)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-00000000c003', 'telegram', '555')
    returning 'ok'$$), 'err 23505', 'el mismo canal e identificador en un tenant choca con la unicidad');  -- 6

-- Aislamiento
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000000a1',
  'select count(*)::text from public.canales_cliente'), '1', 'bot_service del tenant A ve solo sus canales');  -- 7
select is(pg_temp.como('dashboard_service', '00000000-0000-0000-0000-0000000000b1',
  'select count(*)::text from public.canales_cliente'), '1', 'dashboard_service del tenant B ve solo sus canales');  -- 8
select is(pg_temp.como('bot_service', null, 'select count(*)::text from public.canales_cliente'), '0',  -- 9
  'sin tenant en el contexto no se ve nada');
select is(pg_temp.como('platform_admin_readonly', '00000000-0000-0000-0000-0000000000a1',
  'select count(*)::text from public.canales_cliente'), '1',  -- 10
  'platform_admin_readonly también queda acotado al tenant del contexto');
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000000a1',
  $$insert into public.canales_cliente (tenant_id, end_customer_id, canal, identificador)
    values ('00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-00000000c004', 'email', 'x@b.com')
    returning 'ok'$$), 'err 42501', 'un tenant no inserta canales en otro (RLS)');  -- 11
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000000a1',
  $$insert into public.canales_cliente (tenant_id, end_customer_id, canal, identificador)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-00000000c004', 'email', 'x@a.com')
    returning 'ok'$$), 'err 23503', 'la FK compuesta impide colgar un canal de una ficha de otro tenant');  -- 12
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000000a1',
  $$insert into public.canales_cliente (tenant_id, end_customer_id, canal, identificador)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-00000000c003', 'sms', 'x')
    returning 'ok'$$), 'err 23514', 'solo se admiten los canales telegram, email y whatsapp');  -- 13
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000000a1',
  $$insert into public.canales_cliente (tenant_id, end_customer_id, canal, identificador)
    values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-00000000c003', 'email', 'Ana@A.com')
    returning 'ok'$$), 'err 23514', 'el email de un canal va en minúsculas');  -- 14

-- email de end_customers
select is(pg_temp.como('dashboard_service', '00000000-0000-0000-0000-0000000000a1',
  $$update public.end_customers set email = 'Ana@Mail.com' where id = '00000000-0000-0000-0000-00000000c001' returning 'ok'$$),
  'ok', 'se puede cargar un email válido');  -- 15
select is(pg_temp.como('dashboard_service', '00000000-0000-0000-0000-0000000000a1',
  $$update public.end_customers set email = 'ANA@mail.com' where id = '00000000-0000-0000-0000-00000000c003' returning 'ok'$$),
  'err 23505', 'el email es único por tenant sin distinguir mayúsculas');  -- 16
select is(pg_temp.como('dashboard_service', '00000000-0000-0000-0000-0000000000b1',
  $$update public.end_customers set email = 'ana@mail.com' where id = '00000000-0000-0000-0000-00000000c004' returning 'ok'$$),
  'ok', 'el mismo email en otro tenant es otra ficha');  -- 17
select is(pg_temp.como('dashboard_service', '00000000-0000-0000-0000-0000000000a1',
  $$update public.end_customers set email = 'sin-arroba' where id = '00000000-0000-0000-0000-00000000c003' returning 'ok'$$),
  'err 23514', 'un email sin formato válido se rechaza');  -- 18

-- largo del email: 254 caracteres se acepta, 255 con formato válido se rechaza
select is(pg_temp.como('dashboard_service', '00000000-0000-0000-0000-0000000000a1',
  $$update public.end_customers set email = repeat('a', 248) || '@x.com'
    where id = '00000000-0000-0000-0000-00000000c002' returning 'ok'$$),
  'ok', 'un email de 254 caracteres se acepta');  -- 22
select is(pg_temp.como('dashboard_service', '00000000-0000-0000-0000-0000000000a1',
  $$update public.end_customers set email = repeat('a', 249) || '@x.com'
    where id = '00000000-0000-0000-0000-00000000c003' returning 'ok'$$),
  'err 23514', 'un email de 255 caracteres con formato válido se rechaza');  -- 23

-- UPDATE cruzado: ningún rol de aplicación toca canales de otro tenant (RLS: 0 filas afectadas)
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000000a1',
  $$with u as (update public.canales_cliente set verificado_at = '2000-01-01 00:00+00'
                where tenant_id = '00000000-0000-0000-0000-0000000000b1' returning 1)
    select count(*)::text from u$$),
  '0', 'bot_service no actualiza verificado_at de canales de otro tenant');  -- 24
select is(pg_temp.como('dashboard_service', '00000000-0000-0000-0000-0000000000a1',
  $$with u as (update public.canales_cliente set verificado_at = '2000-01-01 00:00+00'
                where tenant_id = '00000000-0000-0000-0000-0000000000b1' returning 1)
    select count(*)::text from u$$),
  '0', 'dashboard_service no actualiza verificado_at de canales de otro tenant');  -- 25
select is(pg_temp.como('dashboard_service', '00000000-0000-0000-0000-0000000000a1',
  $$with u as (update public.canales_cliente set end_customer_id = '00000000-0000-0000-0000-00000000c003'
                where tenant_id = '00000000-0000-0000-0000-0000000000b1' returning 1)
    select count(*)::text from u$$),
  '0', 'dashboard_service no mueve canales de otro tenant a una ficha propia');  -- 26
select is((select count(*) from public.canales_cliente
            where tenant_id = '00000000-0000-0000-0000-0000000000b1'
              and end_customer_id = '00000000-0000-0000-0000-00000000c004'
              and verificado_at = '2026-01-08 10:00+00'), 1::bigint,
  'el canal del tenant B quedó intacto tras los intentos cruzados');  -- 27

-- Grants exactos (por columna)
select is(
  (select string_agg(x.rol || ':' || x.priv || ':' || x.col, ',' order by x.rol, x.priv, x.col) from (
     select r.rol, p.priv, a.attname::text as col
       from unnest(array['bot_service', 'dashboard_service', 'platform_admin_readonly']) as r(rol)
      cross join unnest(array['SELECT', 'INSERT', 'UPDATE', 'REFERENCES']) as p(priv)
      cross join pg_attribute a
      where a.attrelid = 'public.canales_cliente'::regclass and a.attnum > 0 and not a.attisdropped
        and has_column_privilege(r.rol, a.attrelid, a.attnum, p.priv)
  ) x),
  (select string_agg(e, ',' order by e) from unnest(array[
     'bot_service:SELECT:id', 'bot_service:SELECT:tenant_id', 'bot_service:SELECT:end_customer_id',
     'bot_service:SELECT:canal', 'bot_service:SELECT:identificador', 'bot_service:SELECT:verificado_at',
     'bot_service:SELECT:created_at',
     'bot_service:INSERT:id', 'bot_service:INSERT:tenant_id', 'bot_service:INSERT:end_customer_id',
     'bot_service:INSERT:canal', 'bot_service:INSERT:identificador', 'bot_service:INSERT:verificado_at',
     'bot_service:INSERT:created_at', 'bot_service:UPDATE:verificado_at',
     'dashboard_service:SELECT:id', 'dashboard_service:SELECT:tenant_id', 'dashboard_service:SELECT:end_customer_id',
     'dashboard_service:SELECT:canal', 'dashboard_service:SELECT:identificador',
     'dashboard_service:SELECT:verificado_at', 'dashboard_service:SELECT:created_at',
     'dashboard_service:INSERT:id', 'dashboard_service:INSERT:tenant_id', 'dashboard_service:INSERT:end_customer_id',
     'dashboard_service:INSERT:canal', 'dashboard_service:INSERT:identificador',
     'dashboard_service:INSERT:verificado_at', 'dashboard_service:INSERT:created_at',
     'dashboard_service:UPDATE:end_customer_id', 'dashboard_service:UPDATE:verificado_at',
     'platform_admin_readonly:SELECT:id', 'platform_admin_readonly:SELECT:tenant_id',
     'platform_admin_readonly:SELECT:end_customer_id', 'platform_admin_readonly:SELECT:canal',
     'platform_admin_readonly:SELECT:identificador', 'platform_admin_readonly:SELECT:verificado_at',
     'platform_admin_readonly:SELECT:created_at'
  ]) as e),
  'grants por columna de canales_cliente exactos');  -- 19
select ok(not has_table_privilege('bot_service', 'public.canales_cliente', 'DELETE')
          and not has_table_privilege('dashboard_service', 'public.canales_cliente', 'DELETE')
          and not has_any_column_privilege('platform_admin_readonly', 'public.canales_cliente', 'INSERT,UPDATE')
          and not has_table_privilege('platform_admin_readonly', 'public.canales_cliente', 'DELETE'),
  'nadie borra canales directamente y el readonly no escribe');  -- 20
select is(
  (select string_agg(x.rol || ':' || x.priv, ',' order by x.rol, x.priv) from (
     select r.rol, p.priv
       from unnest(array['bot_service', 'dashboard_service', 'platform_admin_readonly']) as r(rol)
      cross join unnest(array['SELECT', 'INSERT', 'UPDATE']) as p(priv)
      where has_column_privilege(r.rol, 'public.end_customers', 'email', p.priv)
  ) x),
  'bot_service:INSERT,bot_service:SELECT,bot_service:UPDATE,dashboard_service:INSERT,dashboard_service:SELECT,'
    || 'dashboard_service:UPDATE,platform_admin_readonly:SELECT',
  'grants exactos sobre end_customers.email');  -- 21

select * from finish();
rollback;
