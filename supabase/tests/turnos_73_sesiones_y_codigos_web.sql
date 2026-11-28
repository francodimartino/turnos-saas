-- Web de reservas: aceptación de la política, sesiones y códigos de acceso (solo hash).
-- Cubre las columnas nuevas de end_customers, la forma de las dos tablas (nada en claro), los bordes de cada check
-- (largo del hash, vencimiento, intentos), los grants exactos, el aislamiento entre tenants con los roles reales
-- y el borrado al anonimizar una ficha o purgar un tenant.
begin;
select plan(85);

do $$
declare
  v_rol text;
begin
  foreach v_rol in array array['bot_service', 'dashboard_service', 'platform_admin_readonly'] loop
    execute format('grant %I to current_user with inherit false, set true', v_rol);
  end loop;
  delete from vault.secrets where name = 'channel_user_hmac_key';
  perform vault.create_secret('clave-de-prueba-hmac', 'channel_user_hmac_key');
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

insert into public.tenants (id, name, slug, status, deactivated_at) values
  ('00000000-0000-0000-0000-0000000000a1', 'A', 'web-a', 'active', null),
  ('00000000-0000-0000-0000-0000000000b1', 'B', 'web-b', 'active', null),
  ('00000000-0000-0000-0000-0000000000c1', 'C', 'web-c', 'purging', now() - interval '100 days');
insert into public.end_customers (id, tenant_id, name) values
  ('00000000-0000-0000-0000-00000000a001', '00000000-0000-0000-0000-0000000000a1', 'Ana'),
  ('00000000-0000-0000-0000-00000000a002', '00000000-0000-0000-0000-0000000000a1', 'Aldo'),
  ('00000000-0000-0000-0000-00000000b001', '00000000-0000-0000-0000-0000000000b1', 'Beto'),
  ('00000000-0000-0000-0000-00000000c001', '00000000-0000-0000-0000-0000000000c1', 'Carla');

-- ---------------------------------------------------------------------------
-- Política de privacidad en la ficha
-- ---------------------------------------------------------------------------
select is((select politica_version is null and politica_aceptada_at is null
             from public.end_customers where id = '00000000-0000-0000-0000-00000000a001'),
  true, 'las fichas existentes quedan con la política sin aceptar (ambas nulas)');  -- 1
select lives_ok($$update public.end_customers set politica_version = 'v1', politica_aceptada_at = now()
  where id = '00000000-0000-0000-0000-00000000a002'$$, 'versión y fecha juntas se aceptan');  -- 2
select throws_ok($$update public.end_customers set politica_version = 'v1'
  where id = '00000000-0000-0000-0000-00000000a001'$$, '23514', null, 'la versión sola se rechaza');  -- 3
select throws_ok($$update public.end_customers set politica_aceptada_at = now()
  where id = '00000000-0000-0000-0000-00000000a001'$$, '23514', null, 'la fecha sola se rechaza');  -- 4
select throws_ok($$update public.end_customers set politica_version = repeat('v', 51), politica_aceptada_at = now()
  where id = '00000000-0000-0000-0000-00000000a001'$$, '23514', null, 'una versión de 51 caracteres se rechaza');  -- 5
select lives_ok($$update public.end_customers set politica_version = repeat('v', 50), politica_aceptada_at = now()
  where id = '00000000-0000-0000-0000-00000000b001'$$, 'una versión de 50 caracteres se acepta');  -- 6
select throws_ok($$update public.end_customers set politica_version = '', politica_aceptada_at = now()
  where id = '00000000-0000-0000-0000-00000000a001'$$, '23514', null, 'una versión vacía se rechaza');  -- 7
select lives_ok($$update public.end_customers set politica_version = null, politica_aceptada_at = null
  where id = '00000000-0000-0000-0000-00000000a002'$$, 'ambas nulas se aceptan');  -- 8

-- ---------------------------------------------------------------------------
-- Forma de las tablas: nada en claro
-- ---------------------------------------------------------------------------
select is(
  (select string_agg(attname::text, ',' order by attname) from pg_attribute
    where attrelid = 'public.sesiones_web'::regclass and attnum > 0 and not attisdropped),
  'created_at,end_customer_id,expira_at,id,revocada_at,tenant_id,token_hash',
  'sesiones_web tiene exactamente estas columnas');
select is(
  (select string_agg(attname::text, ',' order by attname) from pg_attribute
    where attrelid = 'public.codigos_web'::regclass and attnum > 0 and not attisdropped),
  'codigo_hash,created_at,email_hash,end_customer_id,expira_at,id,intentos,ip_hash,tenant_id,usado_at',
  'codigos_web tiene exactamente estas columnas');
select is_empty(
  $$select c.relname, a.attname
      from pg_attribute a join pg_class c on c.oid = a.attrelid
     where c.oid in ('public.sesiones_web'::regclass, 'public.codigos_web'::regclass)
       and a.attnum > 0 and not a.attisdropped
       and a.atttypid in ('text'::regtype, 'varchar'::regtype, 'bytea'::regtype)
       and a.attname !~ '_hash$'$$,
  'toda columna de texto de las dos tablas es un hash: ninguna guarda un valor en claro');

-- el formato del hash lo impone la base: un código de 6 dígitos, un email o una IP no entran en una columna de hash
select throws_ok($$insert into public.sesiones_web (tenant_id, end_customer_id, token_hash, expira_at)
  values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-00000000a001', 'token-en-claro', now() + interval '1 day')$$,
  '23514', null, 'token_hash rechaza un valor que no es un hash');
select throws_ok($$insert into public.codigos_web (tenant_id, email_hash, codigo_hash, expira_at)
  values ('00000000-0000-0000-0000-0000000000a1', '8b5cc4df7eec7d32a7814eca4af047ae33b2d52342667715682e19c25b0b9faa', '123456', now() + interval '5 minutes')$$,
  '23514', null, 'codigo_hash rechaza un código de 6 dígitos en claro');
select throws_ok($$insert into public.codigos_web (tenant_id, email_hash, codigo_hash, expira_at)
  values ('00000000-0000-0000-0000-0000000000a1', 'ana@mail.com', 'd0f631ca1ddba8db3bcfcb9e057cdc98d0379f1bee00e75a545147a27dadd982', now() + interval '5 minutes')$$,
  '23514', null, 'email_hash rechaza un email en claro');
select throws_ok($$insert into public.codigos_web (tenant_id, email_hash, codigo_hash, expira_at, ip_hash)
  values ('00000000-0000-0000-0000-0000000000a1', '8b5cc4df7eec7d32a7814eca4af047ae33b2d52342667715682e19c25b0b9faa', 'd0f631ca1ddba8db3bcfcb9e057cdc98d0379f1bee00e75a545147a27dadd982', now() + interval '5 minutes', '200.1.2.3')$$,
  '23514', null, 'ip_hash rechaza una IP en claro');
select throws_ok($$insert into public.sesiones_web (tenant_id, end_customer_id, token_hash, expira_at)
  values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-00000000a001', repeat('a', 63), now() + interval '1 day')$$,
  '23514', null, 'un hash de 63 caracteres se rechaza');
select throws_ok($$insert into public.sesiones_web (tenant_id, end_customer_id, token_hash, expira_at)
  values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-00000000a001', repeat('a', 65), now() + interval '1 day')$$,
  '23514', null, 'un hash de 65 caracteres se rechaza');
select throws_ok($$insert into public.sesiones_web (tenant_id, end_customer_id, token_hash, expira_at)
  values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-00000000a001', repeat('A', 64), now() + interval '1 day')$$,
  '23514', null, 'un hash con mayúsculas se rechaza');
select lives_ok($$insert into public.sesiones_web (id, tenant_id, end_customer_id, token_hash, expira_at)
  values ('00000000-0000-0000-0000-00000000f001', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-00000000a001', repeat('a', 64), now() + interval '1 day')$$,
  'un hash de 64 caracteres hexadecimales en minúsculas se acepta');
select throws_ok($$insert into public.sesiones_web (tenant_id, end_customer_id, token_hash, expira_at)
  values ('00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-00000000b001', repeat('a', 64), now() + interval '1 day')$$,
  '23505', null, 'el token_hash es único en toda la tabla, también entre tenants');
select lives_ok($$insert into public.codigos_web (tenant_id, email_hash, codigo_hash, expira_at, ip_hash)
  values ('00000000-0000-0000-0000-0000000000a1', '5c88e7a226e11ad1204cb8d30cd5d6ff6cba69bc32da73134928e17c90c54086', '122c597083bd438b7f6d72af75d025948899647711b806bdd2cd82fa69713db3', now() + interval '5 minutes', null)$$,
  'ip_hash puede ser nulo');

-- vencimiento de la sesión: hasta 30 días desde su creación
select lives_ok($$insert into public.sesiones_web (tenant_id, end_customer_id, token_hash, created_at, expira_at)
  values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-00000000a001', '71887144a16bdf3185604be1dabb427fe611eaa17b7c02d2d023a5bff2233752', '2026-01-05 10:00+00', '2026-02-04 10:00+00')$$,
  'una sesión de exactamente 30 días se acepta');
select throws_ok($$insert into public.sesiones_web (tenant_id, end_customer_id, token_hash, created_at, expira_at)
  values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-00000000a001', 'cb30bb2d3ce3689f9bc312857642bbc9842c59364cf794dc05025976b4b92823', '2026-01-05 10:00+00', '2026-02-04 10:00:01+00')$$,
  '23514', null, 'una sesión de 30 días y un segundo se rechaza');
select throws_ok($$insert into public.sesiones_web (tenant_id, end_customer_id, token_hash, created_at, expira_at)
  values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-00000000a001', 'ec18eac8d758b1eba52d3c10d39adc6dd9806472cb4ae069635d383d9086a513', '2026-01-05 10:00+00', '2026-01-05 10:00+00')$$,
  '23514', null, 'una sesión que vence en el mismo instante en que se crea se rechaza');

-- vencimiento e intentos del código: 10 minutos y 5 intentos
select lives_ok($$insert into public.codigos_web (tenant_id, email_hash, codigo_hash, created_at, expira_at)
  values ('00000000-0000-0000-0000-0000000000a1', 'ac0f09c0f8bf5e7a4b063d863255f16d8ce9abe600e288d934cf313bcbff63eb', '9c0abe51c6e6655d81de2d044d4fb194931f058c0426c67c7285d8f5657ed64a', '2026-01-05 10:00+00', '2026-01-05 10:10+00')$$,
  'un código de exactamente 10 minutos se acepta');
select throws_ok($$insert into public.codigos_web (tenant_id, email_hash, codigo_hash, created_at, expira_at)
  values ('00000000-0000-0000-0000-0000000000a1', 'cef7fc13a38180936ffa2635489088778e059f07a5d1beda53f1719d35577631', '7c1c97df17c066924822b0af09a65251554962c61e23329aed04cd19020dc3b8', '2026-01-05 10:00+00', '2026-01-05 10:10:01+00')$$,
  '23514', null, 'un código de 10 minutos y un segundo se rechaza');
select throws_ok($$insert into public.codigos_web (tenant_id, email_hash, codigo_hash, created_at, expira_at)
  values ('00000000-0000-0000-0000-0000000000a1', '449777124b1466a8ed667d0dd4c0620993f59e20fb27b3fa8894e957f8762353', '0012a3fa000c5dc26ee658c3c58e12cecd58d6455cec3d5621f0c787675b38aa', '2026-01-05 10:00+00', '2026-01-05 10:00+00')$$,
  '23514', null, 'un código que vence al crearse se rechaza');
select lives_ok($$insert into public.codigos_web (tenant_id, email_hash, codigo_hash, expira_at, intentos)
  values ('00000000-0000-0000-0000-0000000000a1', '43700797e2f9d4ad38ccf1355df3233453396bfcc8db8e424486e37bae42a9ec', 'd0bf3e6ee1d668de18c9ca200a4f152062f345283ee68cadfe41204f215d75e9', now() + interval '5 minutes', 5)$$,
  'un código con 5 intentos se acepta');
select throws_ok($$insert into public.codigos_web (tenant_id, email_hash, codigo_hash, expira_at, intentos)
  values ('00000000-0000-0000-0000-0000000000a1', 'f33422b95e3b98310adedc93655de579f6e311120ea0c27c3e2317b5116d6afb', '6db53c9d5a2ca72a85ddf3a681c0d9567899f4c48632a2e9b0beeba0d6938485', now() + interval '5 minutes', 6)$$,
  '23514', null, 'un código con 6 intentos se rechaza');
select throws_ok($$update public.codigos_web set intentos = intentos + 1 where codigo_hash = 'd0bf3e6ee1d668de18c9ca200a4f152062f345283ee68cadfe41204f215d75e9'$$,
  '23514', null, 'subir de 5 a 6 intentos un código existente se rechaza');
select throws_ok($$insert into public.codigos_web (tenant_id, email_hash, codigo_hash, expira_at, intentos)
  values ('00000000-0000-0000-0000-0000000000a1', 'f3e2e400523c506868ca413bc55dc213c38e7084caeb10f6f629d8bf5ee9a160', 'f28d5b0d6f8be0da8446dabe79044cb9ed0ffa3150a003936155409fe778b885', now() + interval '5 minutes', -1)$$,
  '23514', null, 'intentos negativos se rechazan');
select throws_ok($$insert into public.codigos_web (tenant_id, email_hash, codigo_hash, expira_at)
  values ('00000000-0000-0000-0000-0000000000ff', 'c33352e36a529830a19080a8f145ca61be645a9395570e0b1c692c71d27bc392', '7ed6a8377b92b49472195f1201af304341daf4abb3643f837eafb38066111f6d', now() + interval '5 minutes')$$,
  '23503', null, 'un código no cuelga de un tenant inexistente');
select throws_ok($$insert into public.sesiones_web (tenant_id, end_customer_id, token_hash, expira_at)
  values ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-00000000b001', '2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881', now() + interval '1 day')$$,
  '23503', null, 'la FK compuesta impide colgar una sesión de una ficha de otro tenant');

-- ---------------------------------------------------------------------------
-- Grants: ningún rol de aplicación toca estas tablas
-- ---------------------------------------------------------------------------
select is(
  (select count(*) from unnest(array['bot_service', 'dashboard_service', 'platform_admin_readonly', 'platform_admin',
                                     'purge_job', 'scheduler_job', 'anon', 'authenticated', 'service_role']) r(rol)
    cross join unnest(array['public.sesiones_web', 'public.codigos_web']) t(tabla)
   where has_table_privilege(r.rol, t.tabla, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
      or has_any_column_privilege(r.rol, t.tabla, 'SELECT,INSERT,UPDATE,REFERENCES')),
  0::bigint, 'ninguno de estos roles tiene privilegios sobre sesiones_web ni codigos_web (solo web_reservas, que se verifica en turnos_78)');
select ok(not has_function_privilege('bot_service', 'public.borrar_sesiones_web_de_ficha_anonimizada()', 'EXECUTE')
          and not has_function_privilege('dashboard_service', 'public.borrar_sesiones_web_de_ficha_anonimizada()', 'EXECUTE')
          and not has_function_privilege('bot_service', 'public.borrar_datos_web_de_tenant_purgado()', 'EXECUTE')
          and not has_function_privilege('dashboard_service', 'public.borrar_datos_web_de_tenant_purgado()', 'EXECUTE'),
  'las funciones de los triggers de borrado no se ejecutan desde los roles de aplicación');
select is(
  (select string_agg(x.rol || ':' || x.priv, ',' order by x.rol, x.priv) from (
     select r.rol, p.priv
       from unnest(array['bot_service', 'dashboard_service', 'platform_admin_readonly']) as r(rol)
      cross join unnest(array['SELECT', 'INSERT', 'UPDATE']) as p(priv)
      where has_column_privilege(r.rol, 'public.end_customers', 'politica_version', p.priv)
         or has_column_privilege(r.rol, 'public.end_customers', 'politica_aceptada_at', p.priv)
  ) x),
  'bot_service:INSERT,bot_service:SELECT,bot_service:UPDATE,dashboard_service:INSERT,dashboard_service:SELECT,'
    || 'dashboard_service:UPDATE,platform_admin_readonly:SELECT',
  'grants exactos sobre las columnas de la política en end_customers (los de la tabla, sin extras)');

-- ---------------------------------------------------------------------------
-- Aislamiento con roles reales. Los roles de aplicación no tienen grants (son tablas de la web), así que se
-- les concede acá, dentro de la transacción, lo que tendrá el rol de la web: RLS sí rige para ellos.
-- ---------------------------------------------------------------------------
grant select, insert, update, delete on public.sesiones_web, public.codigos_web to dashboard_service, bot_service;
grant select on public.sesiones_web, public.codigos_web to platform_admin_readonly;

delete from public.sesiones_web;
delete from public.codigos_web;
insert into public.sesiones_web (id, tenant_id, end_customer_id, token_hash, expira_at) values
  ('00000000-0000-0000-0000-00000000f101', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-00000000a001', 'b56752e29004c2992d52fa1d04adff86eaf7fec30d67a80591757c8dc701c069', now() + interval '1 day'),
  ('00000000-0000-0000-0000-00000000f102', '00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-00000000a002', 'd0c248ce5a5071b24c2af63bafaed7039b70c4531cfa31c7b179127d2fca21e1', now() + interval '1 day'),
  ('00000000-0000-0000-0000-00000000f201', '00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-00000000b001', '320983ce679b69a1fa11436cfe75780398955ba0c7487db46102f854b35ca69a', now() + interval '1 day'),
  ('00000000-0000-0000-0000-00000000f301', '00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-00000000c001', 'e7863eec1ecc9aa61721777bdd315753dd63ae293f0df2ca78cf13978c8f6c9d', now() + interval '1 day');
insert into public.codigos_web (id, tenant_id, email_hash, codigo_hash, expira_at) values
  ('00000000-0000-0000-0000-00000000e101', '00000000-0000-0000-0000-0000000000a1', '8405ae6de3ebde2abffb2531d437a163a31dc42a109c7369b66b93258c7e1d67', '6959097001d10501ac7d54c0bdb8db61420f658f2922cc26e46d536119a31126', now() + interval '5 minutes'),
  ('00000000-0000-0000-0000-00000000e201', '00000000-0000-0000-0000-0000000000b1', 'fce2551fcc23040870d151006816cc39d3831abff948dc646a2f6cb076e08ce0', '103d6254a6d94bacc82e822885185f56c69cb799ec5124c0aa405e386975151b', now() + interval '5 minutes'),
  ('00000000-0000-0000-0000-00000000e301', '00000000-0000-0000-0000-0000000000c1', '1eb85f4d6a3234ce7acb8c51c75930f12e952517e2e389914a6ca8f89a881a0d', '355b1bbfc96725cdce8f4a2708fda310a80e6d13315aec4e5eed2a75fe8032ce', now() + interval '5 minutes');

select is(pg_temp.como('dashboard_service', '00000000-0000-0000-0000-0000000000a1', 'select count(*)::text from public.sesiones_web'), '2',
  'el tenant A ve solo sus sesiones');
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000000b1', 'select count(*)::text from public.sesiones_web'), '1',
  'el tenant B ve solo sus sesiones');
select is(pg_temp.como('dashboard_service', '00000000-0000-0000-0000-0000000000a1', 'select count(*)::text from public.codigos_web'), '1',
  'el tenant A ve solo sus códigos');
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000000b1', 'select count(*)::text from public.codigos_web'), '1',
  'el tenant B ve solo sus códigos');
select is(pg_temp.como('bot_service', null, 'select count(*)::text from public.sesiones_web'), '0',
  'sin tenant en el contexto no se ve ninguna sesión');
select is(pg_temp.como('bot_service', null, 'select count(*)::text from public.codigos_web'), '0',
  'sin tenant en el contexto no se ve ningún código');
select is(pg_temp.como('platform_admin_readonly', '00000000-0000-0000-0000-0000000000a1', 'select count(*)::text from public.sesiones_web'), '2',
  'platform_admin_readonly queda acotado al tenant del contexto en sesiones_web');
select is(pg_temp.como('platform_admin_readonly', '00000000-0000-0000-0000-0000000000a1', 'select count(*)::text from public.codigos_web'), '1',
  'platform_admin_readonly queda acotado al tenant del contexto en codigos_web');
select is(pg_temp.como('dashboard_service', '00000000-0000-0000-0000-0000000000a1',
  $$select count(*)::text from public.sesiones_web where tenant_id = '00000000-0000-0000-0000-0000000000b1'$$), '0',
  'pedir explícitamente las sesiones de otro tenant devuelve 0 filas');
select is(pg_temp.como('dashboard_service', '00000000-0000-0000-0000-0000000000a1',
  $$select count(*)::text from public.codigos_web where codigo_hash = '103d6254a6d94bacc82e822885185f56c69cb799ec5124c0aa405e386975151b'$$), '0',
  'buscar por hash el código de otro tenant devuelve 0 filas');

select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000000a1',
  $$insert into public.sesiones_web (tenant_id, end_customer_id, token_hash, expira_at)
    values ('00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-00000000b001', 'c7b06c06e63acf22ede20704190103a1261577de445b3f5828447b84aad2abe3', now() + interval '1 day') returning 'ok'$$),
  'err 42501', 'un tenant no inserta sesiones en otro (RLS)');
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000000a1',
  $$insert into public.codigos_web (tenant_id, email_hash, codigo_hash, expira_at)
    values ('00000000-0000-0000-0000-0000000000b1', '2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881', 'a1fce4363854ff888cff4b8e7875d600c2682390412a8cf79b37d0b11148b0fa', now() + interval '5 minutes') returning 'ok'$$),
  'err 42501', 'un tenant no inserta códigos en otro (RLS)');
select is(pg_temp.como('dashboard_service', '00000000-0000-0000-0000-0000000000a1',
  $$with u as (update public.sesiones_web set revocada_at = now() where tenant_id = '00000000-0000-0000-0000-0000000000b1' returning 1)
    select count(*)::text from u$$), '0', 'un tenant no revoca sesiones de otro');
select is(pg_temp.como('dashboard_service', '00000000-0000-0000-0000-0000000000a1',
  $$with u as (update public.codigos_web set intentos = 5, usado_at = now() where tenant_id = '00000000-0000-0000-0000-0000000000b1' returning 1)
    select count(*)::text from u$$), '0', 'un tenant no toca los intentos ni el uso de códigos de otro');
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000000a1',
  $$with d as (delete from public.sesiones_web where tenant_id = '00000000-0000-0000-0000-0000000000b1' returning 1) select count(*)::text from d$$),
  '0', 'un tenant no borra sesiones de otro');
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000000a1',
  $$with d as (delete from public.codigos_web where tenant_id = '00000000-0000-0000-0000-0000000000b1' returning 1) select count(*)::text from d$$),
  '0', 'un tenant no borra códigos de otro');
select is(
  (select count(*) from public.sesiones_web where tenant_id = '00000000-0000-0000-0000-0000000000b1' and revocada_at is null
      and token_hash = '320983ce679b69a1fa11436cfe75780398955ba0c7487db46102f854b35ca69a'),
  1::bigint, 'la sesión del tenant B quedó intacta tras los intentos cruzados');
select is(
  (select count(*) from public.codigos_web where tenant_id = '00000000-0000-0000-0000-0000000000b1' and intentos = 0 and usado_at is null
      and codigo_hash = '103d6254a6d94bacc82e822885185f56c69cb799ec5124c0aa405e386975151b'),
  1::bigint, 'el código del tenant B quedó intacto tras los intentos cruzados');
select is(pg_temp.como('dashboard_service', '00000000-0000-0000-0000-0000000000a1',
  $$with u as (update public.codigos_web set intentos = intentos + 1 where tenant_id = '00000000-0000-0000-0000-0000000000a1' returning 1)
    select count(*)::text from u$$), '1', 'el tenant dueño sí suma un intento a su código');
select is(pg_temp.como('dashboard_service', '00000000-0000-0000-0000-0000000000a1',
  $$update public.codigos_web set intentos = 6 where tenant_id = '00000000-0000-0000-0000-0000000000a1' returning 'ok'$$), 'err 23514',
  'ni siquiera el dueño pasa de 5 intentos');

-- Aislamiento de las columnas de la política: con el tenant A no se leen ni se actualizan las de una ficha de B
update public.end_customers set politica_version = 'v7', politica_aceptada_at = '2026-03-01 10:00+00'
 where id = '00000000-0000-0000-0000-00000000b001';
select is(pg_temp.como('dashboard_service', '00000000-0000-0000-0000-0000000000a1',
  $$select count(*)::text from public.end_customers where id = '00000000-0000-0000-0000-00000000b001' and politica_version is not null$$), '0',
  'dashboard_service del tenant A no lee la política aceptada por una ficha de B');
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000000a1',
  $$select count(*)::text from public.end_customers where id = '00000000-0000-0000-0000-00000000b001' and politica_version is not null$$), '0',
  'bot_service del tenant A no lee la política aceptada por una ficha de B');
select is(pg_temp.como('dashboard_service', '00000000-0000-0000-0000-0000000000a1',
  $$with u as (update public.end_customers set politica_version = 'hack', politica_aceptada_at = now()
                where id = '00000000-0000-0000-0000-00000000b001' returning 1) select count(*)::text from u$$), '0',
  'dashboard_service del tenant A no actualiza la política de una ficha de B');
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-0000000000a1',
  $$with u as (update public.end_customers set politica_version = null, politica_aceptada_at = null
                where id = '00000000-0000-0000-0000-00000000b001' returning 1) select count(*)::text from u$$), '0',
  'bot_service del tenant A no borra la política de una ficha de B');
select is(
  (select politica_version = 'v7' and politica_aceptada_at = '2026-03-01 10:00+00'::timestamptz
     from public.end_customers where id = '00000000-0000-0000-0000-00000000b001'),
  true, 'las columnas de la política de la ficha de B quedaron intactas');
select is(pg_temp.como('dashboard_service', '00000000-0000-0000-0000-0000000000b1',
  $$select politica_version from public.end_customers where id = '00000000-0000-0000-0000-00000000b001'$$), 'v7',
  'el tenant dueño sí lee su política');

-- Poda de códigos: se conservan una hora desde su creación (para que los topes por hora cuenten) y después se borran
insert into public.codigos_web (tenant_id, email_hash, codigo_hash, created_at, expira_at) values
  ('00000000-0000-0000-0000-0000000000a1', 'c337d2f29f90ac0d3980daa3a412d9b33e1b14640114d52dfe84e01eee5626f3', 'ef68431537237acec9d9bb750717f9e34ca575cd018f35b8a69c15c44e5e41b0', now() - interval '20 minutes', now() - interval '10 minutes'),
  ('00000000-0000-0000-0000-0000000000b1', '3beb4f5c397138dc861b7a4c69fd0d24631b77d2d84f1312a4edf501ece9ca52', '51c63883f90cbb17659c22e818b82b12b38cb89e5ea98e695388c621de13273a', now() - interval '20 minutes', now() - interval '10 minutes');
insert into public.codigos_web (tenant_id, email_hash, codigo_hash, created_at, expira_at) values
  ('00000000-0000-0000-0000-0000000000a1', '7c10cfbc5a51fc152fa4ff70437ec6e3508f433406a19e275a4d1bc07db1b1de', '84ff9a4fc6c2eddec8fbeb9f46248556f21a133fce591703bd2a95ea25fe074a', now() - interval '2 hours', now() - interval '110 minutes'),
  ('00000000-0000-0000-0000-0000000000b1', 'db5c718e7121595cd49fa4d7fea2a6d560119b1738d14b7aad6ed510072706fc', '880ff7555d80afac2c831045d1c8dbe64baa08a2580f307cd8c8c1d0453d9bc4', now() - interval '2 hours', now() - interval '110 minutes');
select is((select count(*) from public.codigos_web where codigo_hash in
  ('84ff9a4fc6c2eddec8fbeb9f46248556f21a133fce591703bd2a95ea25fe074a', '880ff7555d80afac2c831045d1c8dbe64baa08a2580f307cd8c8c1d0453d9bc4', 'ef68431537237acec9d9bb750717f9e34ca575cd018f35b8a69c15c44e5e41b0', '51c63883f90cbb17659c22e818b82b12b38cb89e5ea98e695388c621de13273a')), 4::bigint,
  'hay en cada tenant un código vencido de hace 20 minutos y otro de hace 2 horas');
-- el rol de aplicación no tiene permiso de borrado: la poda la hace el trigger por él
revoke delete on public.codigos_web from dashboard_service, bot_service;
select ok(not has_table_privilege('dashboard_service', 'public.codigos_web', 'DELETE')
          and not has_table_privilege('bot_service', 'public.codigos_web', 'DELETE'),
  'dashboard_service y bot_service no tienen DELETE sobre codigos_web');
select is(pg_temp.como('dashboard_service', '00000000-0000-0000-0000-0000000000a1',
  $$delete from public.codigos_web where tenant_id = '00000000-0000-0000-0000-0000000000a1' returning 'ok'$$), 'err 42501',
  'sin DELETE el rol no borra códigos directamente');
select is(pg_temp.como('dashboard_service', '00000000-0000-0000-0000-0000000000a1',
  $$insert into public.codigos_web (tenant_id, email_hash, codigo_hash, expira_at)
    values ('00000000-0000-0000-0000-0000000000a1', '5b5c739dd9f1109e71f7979465f5e65c7dbcc950f040776c0e7090f535bd5faf', 'a8ef756971c5055483ff64d1b9ea0b7cf78f904f3b0d65648dbf5b3d5388abc8', now() + interval '10 minutes') returning 'ok'$$), 'ok',
  'el rol sin permiso de borrado inserta un código nuevo');
select is((select count(*) from public.codigos_web where codigo_hash = '84ff9a4fc6c2eddec8fbeb9f46248556f21a133fce591703bd2a95ea25fe074a'), 0::bigint,
  'al insertar un código se borra el de más de una hora del mismo tenant');
select is((select count(*) from public.codigos_web where codigo_hash = 'ef68431537237acec9d9bb750717f9e34ca575cd018f35b8a69c15c44e5e41b0'), 1::bigint,
  'el vencido de hace 20 minutos se conserva: sigue contando para los topes por hora');
select is((select count(*) from public.codigos_web where codigo_hash = '880ff7555d80afac2c831045d1c8dbe64baa08a2580f307cd8c8c1d0453d9bc4'), 1::bigint,
  'no se toca el de más de una hora de otro tenant');
select is((select count(*) from public.codigos_web where codigo_hash = 'a8ef756971c5055483ff64d1b9ea0b7cf78f904f3b0d65648dbf5b3d5388abc8'), 1::bigint,
  'el código nuevo y vigente se conserva');
select is((select count(*) from public.codigos_web where codigo_hash = '6959097001d10501ac7d54c0bdb8db61420f658f2922cc26e46d536119a31126'), 1::bigint,
  'los códigos vigentes del tenant no se podan');
select is(public.podar_codigos_web_vencidos(), 1, 'la poda global devuelve cuántos borró');
select is((select count(*) from public.codigos_web where codigo_hash = '880ff7555d80afac2c831045d1c8dbe64baa08a2580f307cd8c8c1d0453d9bc4'), 0::bigint,
  'la poda global borra el de más de una hora de cualquier tenant');
select is((select count(*) from public.codigos_web where codigo_hash in ('ef68431537237acec9d9bb750717f9e34ca575cd018f35b8a69c15c44e5e41b0', '51c63883f90cbb17659c22e818b82b12b38cb89e5ea98e695388c621de13273a')), 2::bigint,
  'la poda global conserva los vencidos de hace 20 minutos');
select is((select count(*) from public.codigos_web), 6::bigint, 'la poda global deja los de la última hora');
select is(public.podar_codigos_web_vencidos(), 0, 'una segunda poda no encuentra nada');
select ok(not has_function_privilege('bot_service', 'public.podar_codigos_web_vencidos()', 'EXECUTE')
          and not has_function_privilege('dashboard_service', 'public.podar_codigos_web_vencidos()', 'EXECUTE')
          and not has_function_privilege('scheduler_job', 'public.podar_codigos_web_vencidos()', 'EXECUTE')
          and not has_function_privilege('anon', 'public.podar_codigos_web_vencidos()', 'EXECUTE'),
  'nadie ejecuta la poda global todavía');

-- ---------------------------------------------------------------------------
-- Anonimización de la ficha y purga del tenant
-- ---------------------------------------------------------------------------
select public.anonymize_end_customer_core('00000000-0000-0000-0000-00000000a001', 'system', null);
select is((select count(*) from public.sesiones_web where end_customer_id = '00000000-0000-0000-0000-00000000a001'), 0::bigint,
  'anonimizar la ficha borra sus sesiones de la web');
select is((select count(*) from public.sesiones_web where end_customer_id = '00000000-0000-0000-0000-00000000a002'), 1::bigint,
  'las sesiones de otras fichas del mismo tenant quedan');
select is((select count(*) from public.sesiones_web where tenant_id = '00000000-0000-0000-0000-0000000000b1'), 1::bigint,
  'anonimizar no toca las sesiones de otro tenant');
select is((select count(*) from public.codigos_web where tenant_id = '00000000-0000-0000-0000-0000000000a1'), 3::bigint,
  'los códigos sin ficha ligada no se tocan al anonimizar una ficha (se podan pasada una hora)');

update public.tenants set status = 'purged' where id = '00000000-0000-0000-0000-0000000000c1';
select is((select count(*) from public.sesiones_web where tenant_id = '00000000-0000-0000-0000-0000000000c1'), 0::bigint,
  'al purgar el tenant no queda ninguna sesión de la web');
select is((select count(*) from public.codigos_web where tenant_id = '00000000-0000-0000-0000-0000000000c1'), 0::bigint,
  'al purgar el tenant no queda ningún código de la web');
select is((select count(*) from public.sesiones_web where tenant_id in ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000b1')), 2::bigint,
  'la purga de un tenant no toca las sesiones de otros');
select is((select count(*) from public.codigos_web where tenant_id in ('00000000-0000-0000-0000-0000000000a1', '00000000-0000-0000-0000-0000000000b1')), 5::bigint,
  'la purga de un tenant no toca los códigos de otros');

select * from finish();
rollback;
