-- avisos_negocio_config (quién recibe los avisos al negocio y por qué canal) y canal 'email' en notifications_log.
-- Estructura, grants por columna exactos, aislamiento entre tenants con los roles reales (set role + contexto de
-- tenant), regla por defecto (owner y quienes aprueban turnos), que el negocio nunca quede sin destinatario de
-- email, WhatsApp deshabilitado y registro de avisos por email.
begin;
select plan(80);

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

-- Ejecuta una sentencia como p_rol con el contexto de tenant dado ('' = sin contexto) y revierte sus efectos.
-- Devuelve 'ok <filas>' o 'err <sqlstate> <mensaje>'.
create function pg_temp.ejecutar(p_rol text, p_ctx text, p_sql text) returns text
language plpgsql as $$
declare
  v_n bigint;
begin
  execute format('set role %I', p_rol);
  perform set_config('app.current_tenant_id', coalesce(p_ctx, ''), true);
  begin
    execute p_sql;
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

-- Igual, pero conserva los efectos (para comprobar después, como dueño, qué quedó en la tabla).
create function pg_temp.escribir(p_rol text, p_ctx text, p_sql text) returns text
language plpgsql as $$
declare
  v_n bigint;
begin
  execute format('set role %I', p_rol);
  perform set_config('app.current_tenant_id', coalesce(p_ctx, ''), true);
  begin
    execute p_sql;
    get diagnostics v_n = row_count;
    reset role;
    return 'ok ' || v_n;
  exception when others then
    reset role;
    return 'err ' || sqlstate || ' ' || sqlerrm;
  end;
end
$$;

-- Cantidad de filas que ve p_sql como p_rol con ese contexto.
create function pg_temp.contar(p_rol text, p_ctx text, p_sql text) returns text
language plpgsql as $$
declare
  v_n bigint;
begin
  execute format('set role %I', p_rol);
  perform set_config('app.current_tenant_id', coalesce(p_ctx, ''), true);
  begin
    execute 'select count(*) from (' || p_sql || ') q' into v_n;
    reset role;
    return v_n::text;
  exception when others then
    reset role;
    return 'err ' || sqlstate;
  end;
end
$$;

insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000072a1', 'A', 'avisos-a'),
  ('00000000-0000-0000-0000-0000000072a2', 'B', 'avisos-b');
-- A: owner (b1), aprobador (b2), staff común (b3), aprobador inactivo (b4). B: owner (b5).
insert into public.tenant_users (id, tenant_id, is_owner, is_active) values
  ('00000000-0000-0000-0000-0000000072b1', '00000000-0000-0000-0000-0000000072a1', true, true),
  ('00000000-0000-0000-0000-0000000072b2', '00000000-0000-0000-0000-0000000072a1', false, true),
  ('00000000-0000-0000-0000-0000000072b3', '00000000-0000-0000-0000-0000000072a1', false, true),
  ('00000000-0000-0000-0000-0000000072b4', '00000000-0000-0000-0000-0000000072a1', false, false),
  ('00000000-0000-0000-0000-0000000072b5', '00000000-0000-0000-0000-0000000072a2', true, true);
insert into public.tenant_user_permissions (tenant_id, tenant_user_id, permission_key) values
  ('00000000-0000-0000-0000-0000000072a1', '00000000-0000-0000-0000-0000000072b2', 'approve_bookings'),
  ('00000000-0000-0000-0000-0000000072a1', '00000000-0000-0000-0000-0000000072b4', 'approve_bookings');
insert into public.end_customers (id, tenant_id, channel, channel_user_id) values
  ('00000000-0000-0000-0000-0000000072e1', '00000000-0000-0000-0000-0000000072a1', 'telegram', 'avisos-u1'),
  ('00000000-0000-0000-0000-0000000072e2', '00000000-0000-0000-0000-0000000072a2', 'telegram', 'avisos-u2');
-- Filas propias de cada tenant (la de A no suma destinatarios: b3 no recibe).
insert into public.avisos_negocio_config (tenant_id, tenant_user_id, recibe_avisos, canal_email) values
  ('00000000-0000-0000-0000-0000000072a1', '00000000-0000-0000-0000-0000000072b3', false, true),
  ('00000000-0000-0000-0000-0000000072a2', '00000000-0000-0000-0000-0000000072b5', true, true);

-- ---------------------------------------------------------------------------
-- Estructura
-- ---------------------------------------------------------------------------
select has_table('public', 'avisos_negocio_config', 'existe avisos_negocio_config');  -- 1
select ok((select relrowsecurity from pg_class where oid = 'public.avisos_negocio_config'::regclass),  -- 2
  'avisos_negocio_config tiene RLS activada');
select ok(exists (select 1 from pg_policies where schemaname = 'public' and tablename = 'avisos_negocio_config'  -- 3
                   and policyname = 'tenant_isolation'),
  'avisos_negocio_config tiene la policy tenant_isolation');
select is((select count(*) from pg_policies where schemaname = 'public' and tablename = 'avisos_negocio_config'),  -- 4
  1::bigint, 'avisos_negocio_config no tiene más policies que tenant_isolation');
select is(  -- 5
  (select string_agg(attname::text, ',' order by attnum) from pg_attribute
    where attrelid = 'public.avisos_negocio_config'::regclass and attnum > 0 and not attisdropped),
  'tenant_id,tenant_user_id,recibe_avisos,canal_email,canal_whatsapp', 'columnas de avisos_negocio_config');
select is(  -- 6
  (select string_agg(a.attname::text, ',' order by k.ord)
     from pg_constraint c
     cross join lateral unnest(c.conkey) with ordinality k(attnum, ord)
     join pg_attribute a on a.attrelid = c.conrelid and a.attnum = k.attnum
    where c.conrelid = 'public.avisos_negocio_config'::regclass and c.contype = 'p'),
  'tenant_id,tenant_user_id', 'unicidad por (tenant_id, tenant_user_id): clave primaria compuesta');
select is(  -- 7
  (select count(*) from pg_constraint
    where conrelid = 'public.avisos_negocio_config'::regclass and contype = 'f'
      and confrelid = 'public.tenant_users'::regclass and array_length(conkey, 1) = 2),
  1::bigint, 'clave foránea compuesta (tenant_id, tenant_user_id) hacia tenant_users');
select ok(  -- 8
  (select bool_and(attnotnull) from pg_attribute
    where attrelid = 'public.avisos_negocio_config'::regclass and attnum > 0 and not attisdropped),
  'todas las columnas son not null');
select ok(  -- 9
  obj_description('public.avisos_negocio_config'::regclass, 'pg_class') like '%approve_bookings%',
  'la regla por defecto está documentada en la tabla');

-- ---------------------------------------------------------------------------
-- Grants exactos
-- ---------------------------------------------------------------------------
select is(  -- 10
  (select string_agg(attname::text, ',' order by attnum) from pg_attribute
    where attrelid = 'public.avisos_negocio_config'::regclass and attnum > 0 and not attisdropped
      and has_column_privilege('dashboard_service', attrelid, attnum, 'SELECT')),
  'tenant_id,tenant_user_id,recibe_avisos,canal_email,canal_whatsapp', 'dashboard_service: SELECT de todas las columnas');
select is(  -- 11
  (select string_agg(attname::text, ',' order by attnum) from pg_attribute
    where attrelid = 'public.avisos_negocio_config'::regclass and attnum > 0 and not attisdropped
      and has_column_privilege('dashboard_service', attrelid, attnum, 'INSERT')),
  'tenant_id,tenant_user_id,recibe_avisos,canal_email,canal_whatsapp', 'dashboard_service: INSERT de las cinco columnas');
select is(  -- 12
  (select string_agg(attname::text, ',' order by attnum) from pg_attribute
    where attrelid = 'public.avisos_negocio_config'::regclass and attnum > 0 and not attisdropped
      and has_column_privilege('dashboard_service', attrelid, attnum, 'UPDATE')),
  'recibe_avisos,canal_email,canal_whatsapp', 'dashboard_service: UPDATE solo de las preferencias, no de la identidad de la fila');
select is(  -- 13
  (select string_agg(attname::text, ',' order by attnum) from pg_attribute
    where attrelid = 'public.avisos_negocio_config'::regclass and attnum > 0 and not attisdropped
      and has_column_privilege('platform_admin_readonly', attrelid, attnum, 'SELECT')),
  'tenant_id,tenant_user_id,recibe_avisos,canal_email,canal_whatsapp', 'platform_admin_readonly: SELECT de todas las columnas');
select is_empty(  -- 14
  $$select r from unnest(array['bot_service', 'platform_admin', 'purge_job', 'scheduler_job', 'anon',
                               'authenticated', 'service_role']) r
     where has_any_column_privilege(r, 'public.avisos_negocio_config', 'SELECT,INSERT,UPDATE,REFERENCES')$$,
  'bot_service, platform_admin, purge_job, scheduler_job y los roles de Supabase no tienen ningún privilegio de columna');
select is_empty(  -- 15
  $$select r from unnest(array['bot_service', 'platform_admin', 'platform_admin_readonly',
                               'purge_job', 'scheduler_job', 'anon', 'authenticated', 'service_role']) r
     where has_table_privilege(r, 'public.avisos_negocio_config', 'DELETE,TRUNCATE,TRIGGER,INSERT,UPDATE,SELECT,REFERENCES')$$,
  'los demás roles no tienen privilegio de tabla');
select ok(  -- 15b
  has_table_privilege('dashboard_service', 'public.avisos_negocio_config', 'DELETE')
  and not has_table_privilege('dashboard_service', 'public.avisos_negocio_config', 'TRUNCATE,TRIGGER,REFERENCES,INSERT,UPDATE,SELECT'),
  'dashboard_service tiene DELETE de tabla (volver al default) y nada más a nivel de tabla');
select is(  -- 16
  pg_temp.ejecutar('dashboard_service', '00000000-0000-0000-0000-0000000072a1',
    $q$delete from public.avisos_negocio_config where tenant_id = '00000000-0000-0000-0000-0000000072a1'$q$),
  'ok 1', 'dashboard_service borra la fila de su tenant (vuelve al default)');
select is(  -- 16b
  pg_temp.ejecutar('dashboard_service', '00000000-0000-0000-0000-0000000072a2',
    $q$delete from public.avisos_negocio_config where tenant_id = '00000000-0000-0000-0000-0000000072a1'$q$),
  'ok 0', 'con contexto B no se borra la fila de A');

-- ---------------------------------------------------------------------------
-- Aislamiento entre tenants con los roles reales
-- ---------------------------------------------------------------------------
select is(pg_temp.contar('dashboard_service', '00000000-0000-0000-0000-0000000072a1',  -- 17
  $q$select 1 from public.avisos_negocio_config$q$), '1', 'dashboard_service con contexto A ve solo la fila de A');
select is(pg_temp.contar('dashboard_service', '00000000-0000-0000-0000-0000000072a1',  -- 18
  $q$select 1 from public.avisos_negocio_config where tenant_id = '00000000-0000-0000-0000-0000000072a2'$q$),
  '0', 'con contexto A no se lee la configuración de B');
select is(pg_temp.contar('dashboard_service', '00000000-0000-0000-0000-0000000072a2',  -- 19
  $q$select 1 from public.avisos_negocio_config where tenant_id = '00000000-0000-0000-0000-0000000072a1'$q$),
  '0', 'con contexto B no se lee la configuración de A');
select is(pg_temp.contar('dashboard_service', null,  -- 20
  $q$select 1 from public.avisos_negocio_config$q$), '0', 'sin contexto de tenant no se lee nada');
select is(pg_temp.contar('platform_admin_readonly', '00000000-0000-0000-0000-0000000072a1',  -- 21
  $q$select 1 from public.avisos_negocio_config$q$), '1',
  'platform_admin_readonly lee solo la configuración del tenant que impersona, no la de B');
select is(pg_temp.contar('platform_admin', '00000000-0000-0000-0000-0000000072a1',  -- 22
  $q$select 1 from public.avisos_negocio_config$q$), 'err 42501', 'platform_admin no tiene acceso a la tabla');
select is(pg_temp.contar('bot_service', '00000000-0000-0000-0000-0000000072a1',  -- 23
  $q$select 1 from public.avisos_negocio_config$q$), 'err 42501', 'bot_service no tiene acceso a la tabla');
select is(pg_temp.escribir('dashboard_service', '00000000-0000-0000-0000-0000000072a2',  -- 24
  $q$update public.avisos_negocio_config set recibe_avisos = false, canal_email = false
      where tenant_id = '00000000-0000-0000-0000-0000000072a1'$q$),
  'ok 0', 'con contexto B, actualizar la fila de A no afecta ninguna fila');
select is(  -- 25
  (select recibe_avisos::text || canal_email::text from public.avisos_negocio_config
    where tenant_id = '00000000-0000-0000-0000-0000000072a1'),
  'falsetrue', 'la fila de A quedó intacta tras el intento de B');
select is(pg_temp.escribir('dashboard_service', '00000000-0000-0000-0000-0000000072a1',  -- 26
  $q$update public.avisos_negocio_config set recibe_avisos = false
      where tenant_id = '00000000-0000-0000-0000-0000000072a2'$q$),
  'ok 0', 'con contexto A, actualizar la fila de B no afecta ninguna fila');
select is(  -- 27
  (select recibe_avisos::text from public.avisos_negocio_config
    where tenant_id = '00000000-0000-0000-0000-0000000072a2'),
  'true', 'la fila de B quedó intacta tras el intento de A');
select is(pg_temp.ejecutar('dashboard_service', '00000000-0000-0000-0000-0000000072a2',  -- 28
  $q$insert into public.avisos_negocio_config (tenant_id, tenant_user_id)
     values ('00000000-0000-0000-0000-0000000072a1', '00000000-0000-0000-0000-0000000072b2')$q$),
  'err 42501 new row violates row-level security policy for table "avisos_negocio_config"',
  'con contexto B no se inserta una fila de A');
select is(pg_temp.ejecutar('dashboard_service', '00000000-0000-0000-0000-0000000072a2',  -- 29
  $q$insert into public.avisos_negocio_config (tenant_id, tenant_user_id)
     values ('00000000-0000-0000-0000-0000000072a2', '00000000-0000-0000-0000-0000000072b2')$q$),
  'err 23503 insert or update on table "avisos_negocio_config" violates foreign key constraint "avisos_negocio_config_tenant_id_tenant_user_id_fkey"',
  'no se configura a un miembro de otro tenant (clave foránea compuesta)');
select is(pg_temp.ejecutar('dashboard_service', '00000000-0000-0000-0000-0000000072a1',  -- 30
  $q$insert into public.avisos_negocio_config (tenant_id, tenant_user_id, recibe_avisos)
     values ('00000000-0000-0000-0000-0000000072a1', '00000000-0000-0000-0000-0000000072b2', true)$q$),
  'ok 1', 'con contexto A se inserta la fila de un miembro de A');
select is(pg_temp.ejecutar('dashboard_service', '00000000-0000-0000-0000-0000000072a1',  -- 31
  $q$insert into public.avisos_negocio_config (tenant_id, tenant_user_id)
     values ('00000000-0000-0000-0000-0000000072a1', '00000000-0000-0000-0000-0000000072b3')$q$),
  'err 23505 duplicate key value violates unique constraint "avisos_negocio_config_pkey"',
  'una sola fila por (tenant, miembro)');
select is(pg_temp.ejecutar('dashboard_service', '00000000-0000-0000-0000-0000000072a1',  -- 32
  $q$update public.avisos_negocio_config set tenant_id = '00000000-0000-0000-0000-0000000072a2'$q$),
  'err 42501 permission denied for table avisos_negocio_config', 'no se cambia el tenant de una fila (sin UPDATE de tenant_id)');
select is(pg_temp.ejecutar('platform_admin_readonly', '00000000-0000-0000-0000-0000000072a1',  -- 33
  $q$update public.avisos_negocio_config set recibe_avisos = true$q$),
  'err 42501 permission denied for table avisos_negocio_config', 'platform_admin_readonly no escribe');

-- ---------------------------------------------------------------------------
-- WhatsApp aparece pero queda deshabilitado
-- ---------------------------------------------------------------------------
select is(pg_temp.ejecutar('dashboard_service', '00000000-0000-0000-0000-0000000072a1',  -- 34
  $q$insert into public.avisos_negocio_config (tenant_id, tenant_user_id, canal_whatsapp)
     values ('00000000-0000-0000-0000-0000000072a1', '00000000-0000-0000-0000-0000000072b2', true)$q$),
  'err 23514 new row for relation "avisos_negocio_config" violates check constraint "avisos_negocio_config_whatsapp_deshabilitado"',
  'no se puede activar WhatsApp al insertar');
select is(pg_temp.ejecutar('dashboard_service', '00000000-0000-0000-0000-0000000072a1',  -- 35
  $q$update public.avisos_negocio_config set canal_whatsapp = true
      where tenant_id = '00000000-0000-0000-0000-0000000072a1'$q$),
  'err 23514 new row for relation "avisos_negocio_config" violates check constraint "avisos_negocio_config_whatsapp_deshabilitado"',
  'no se puede activar WhatsApp al actualizar');
select is(pg_temp.ejecutar('dashboard_service', '00000000-0000-0000-0000-0000000072a1',  -- 36
  $q$update public.avisos_negocio_config set canal_whatsapp = false
      where tenant_id = '00000000-0000-0000-0000-0000000072a1'$q$),
  'ok 1', 'dejarlo en falso es válido');
select col_default_is('public', 'avisos_negocio_config', 'canal_whatsapp', 'false', 'canal_whatsapp nace en falso');  -- 37

-- ---------------------------------------------------------------------------
-- Regla por defecto (sin fila: owner y quienes aprueban turnos) y destinatarios de email
-- ---------------------------------------------------------------------------
select is(public.avisos_negocio_destinatarios_email('00000000-0000-0000-0000-0000000072a1'), 2,  -- 38
  'A: por defecto reciben el owner (b1) y el aprobador activo (b2); no el staff común ni el aprobador inactivo');
select is(public.avisos_negocio_destinatarios_email('00000000-0000-0000-0000-0000000072a2'), 1,  -- 39
  'B: recibe su owner');
select is(public.avisos_negocio_destinatarios_email('00000000-0000-0000-0000-0000000072a9'), 0,  -- 40
  'un tenant sin miembros no tiene destinatarios');

select lives_ok(  -- 41
  $$insert into public.avisos_negocio_config (tenant_id, tenant_user_id, recibe_avisos, canal_email)
    values ('00000000-0000-0000-0000-0000000072a1', '00000000-0000-0000-0000-0000000072b4', true, true)$$,
  'configurar a un miembro inactivo no suma destinatarios ni falla');
select is(public.avisos_negocio_destinatarios_email('00000000-0000-0000-0000-0000000072a1'), 2,  -- 42
  'el miembro inactivo no cuenta como destinatario');
select lives_ok(  -- 43
  $$update public.avisos_negocio_config set recibe_avisos = true
     where tenant_id = '00000000-0000-0000-0000-0000000072a1' and tenant_user_id = '00000000-0000-0000-0000-0000000072b3'$$,
  'el staff común puede pasar a recibir avisos');
select is(public.avisos_negocio_destinatarios_email('00000000-0000-0000-0000-0000000072a1'), 3,  -- 44
  'ahora reciben b1, b2 (default) y b3 (por su fila)');
select lives_ok(  -- 45
  $$insert into public.avisos_negocio_config (tenant_id, tenant_user_id, recibe_avisos, canal_email) values
      ('00000000-0000-0000-0000-0000000072a1', '00000000-0000-0000-0000-0000000072b1', false, true),
      ('00000000-0000-0000-0000-0000000072a1', '00000000-0000-0000-0000-0000000072b2', true, false)$$,
  'el owner puede dejar de recibir y el aprobador puede apagar el email mientras quede otro destinatario');
select is(public.avisos_negocio_destinatarios_email('00000000-0000-0000-0000-0000000072a1'), 1,  -- 46
  'la fila reemplaza al default: queda solo b3');
select throws_ok(  -- 47
  $$update public.avisos_negocio_config set recibe_avisos = false
     where tenant_id = '00000000-0000-0000-0000-0000000072a1' and tenant_user_id = '00000000-0000-0000-0000-0000000072b3'$$,
  '23514', 'el negocio no puede quedar sin ningún destinatario de avisos por email',
  'apagar los avisos del último destinatario se rechaza');
select throws_ok(  -- 48
  $$update public.avisos_negocio_config set canal_email = false
     where tenant_id = '00000000-0000-0000-0000-0000000072a1' and tenant_user_id = '00000000-0000-0000-0000-0000000072b3'$$,
  '23514', 'el negocio no puede quedar sin ningún destinatario de avisos por email',
  'apagar el canal email del último destinatario se rechaza');
select throws_ok(  -- 49
  $$update public.avisos_negocio_config set recibe_avisos = false
     where tenant_id = '00000000-0000-0000-0000-0000000072a2'$$,
  '23514', 'el negocio no puede quedar sin ningún destinatario de avisos por email',
  'B: apagar los avisos de su único destinatario se rechaza');
select is(public.avisos_negocio_destinatarios_email('00000000-0000-0000-0000-0000000072a1'), 1,  -- 50
  'tras los rechazos A conserva su destinatario');
select lives_ok(  -- 51
  $$set constraints all deferred;
    update public.avisos_negocio_config set recibe_avisos = false
     where tenant_id = '00000000-0000-0000-0000-0000000072a1' and tenant_user_id = '00000000-0000-0000-0000-0000000072b3';
    update public.avisos_negocio_config set canal_email = true
     where tenant_id = '00000000-0000-0000-0000-0000000072a1' and tenant_user_id = '00000000-0000-0000-0000-0000000072b2';
    set constraints all immediate$$,
  'intercambiar destinatarios en una misma transacción vale si al final queda alguno');
select throws_ok(  -- 52
  $$set constraints all deferred;
    update public.avisos_negocio_config set canal_email = false
     where tenant_id = '00000000-0000-0000-0000-0000000072a1' and tenant_user_id = '00000000-0000-0000-0000-0000000072b2';
    set constraints all immediate$$,
  '23514', 'el negocio no puede quedar sin ningún destinatario de avisos por email',
  'diferido o no, al final de la transacción debe quedar un destinatario');
select is(pg_temp.ejecutar('dashboard_service', '00000000-0000-0000-0000-0000000072a1',  -- 53
  $q$update public.avisos_negocio_config set canal_email = false
      where tenant_id = '00000000-0000-0000-0000-0000000072a1' and tenant_user_id = '00000000-0000-0000-0000-0000000072b2'$q$),
  'err 23514 el negocio no puede quedar sin ningún destinatario de avisos por email',
  'dashboard_service recibe el mismo rechazo con su rol real');
select is(pg_temp.ejecutar('dashboard_service', '00000000-0000-0000-0000-0000000072a1',  -- 54
  $q$update public.avisos_negocio_config set recibe_avisos = true
      where tenant_id = '00000000-0000-0000-0000-0000000072a1' and tenant_user_id = '00000000-0000-0000-0000-0000000072b3'$q$),
  'ok 1', 'dashboard_service suma un destinatario con su rol real');

-- ---------------------------------------------------------------------------
-- La garantía también vale ante cambios del staff, borrados de la fila y concurrencia
-- ---------------------------------------------------------------------------
insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000072d1', 'AVISOS-F', 'avisos-f'),
  ('00000000-0000-0000-0000-0000000072d2', 'AVISOS-G', 'avisos-g'),
  ('00000000-0000-0000-0000-0000000072d3', 'AVISOS-H', 'avisos-h'),
  ('00000000-0000-0000-0000-0000000072d4', 'AVISOS-J', 'avisos-j'),
  ('00000000-0000-0000-0000-0000000072d5', 'AVISOS-K', 'avisos-k'),
  ('00000000-0000-0000-0000-0000000072d6', 'AVISOS-L', 'avisos-l'),
  ('00000000-0000-0000-0000-0000000072d7', 'AVISOS-M', 'avisos-m');
-- F: owners 1a y 1d (con aviso apagado), aprobador 1b (único destinatario), staff común 1c.
-- G: owner 2a (destinatario por default), owner 2b (aviso apagado).
-- H: owner 3a (aviso apagado) y aprobador 3b con fila explícita: 3b es el único destinatario.
-- J: owner 4a (aviso apagado), staff común 4b con fila explícita: 4b es el único destinatario.
-- K, L y M: sin filas, para observar el lock de cada camino.
insert into public.tenant_users (id, tenant_id, is_owner, is_active) values
  ('00000000-0000-0000-0000-00000000721a', '00000000-0000-0000-0000-0000000072d1', true, true), ('00000000-0000-0000-0000-00000000721b', '00000000-0000-0000-0000-0000000072d1', false, true), ('00000000-0000-0000-0000-00000000721c', '00000000-0000-0000-0000-0000000072d1', false, true), ('00000000-0000-0000-0000-00000000721d', '00000000-0000-0000-0000-0000000072d1', true, true),
  ('00000000-0000-0000-0000-00000000722a', '00000000-0000-0000-0000-0000000072d2', true, true), ('00000000-0000-0000-0000-00000000722b', '00000000-0000-0000-0000-0000000072d2', true, true),
  ('00000000-0000-0000-0000-00000000723a', '00000000-0000-0000-0000-0000000072d3', true, true), ('00000000-0000-0000-0000-00000000723b', '00000000-0000-0000-0000-0000000072d3', false, true),
  ('00000000-0000-0000-0000-00000000724a', '00000000-0000-0000-0000-0000000072d4', true, true), ('00000000-0000-0000-0000-00000000724b', '00000000-0000-0000-0000-0000000072d4', false, true),
  ('00000000-0000-0000-0000-00000000725a', '00000000-0000-0000-0000-0000000072d5', true, true), ('00000000-0000-0000-0000-00000000725b', '00000000-0000-0000-0000-0000000072d5', false, true),
  ('00000000-0000-0000-0000-00000000726a', '00000000-0000-0000-0000-0000000072d6', true, true), ('00000000-0000-0000-0000-00000000726b', '00000000-0000-0000-0000-0000000072d6', false, true),
  ('00000000-0000-0000-0000-00000000727a', '00000000-0000-0000-0000-0000000072d7', true, true), ('00000000-0000-0000-0000-00000000727b', '00000000-0000-0000-0000-0000000072d7', false, true);
insert into public.tenant_user_permissions (tenant_id, tenant_user_id, permission_key) values
  ('00000000-0000-0000-0000-0000000072d1', '00000000-0000-0000-0000-00000000721b', 'approve_bookings'), ('00000000-0000-0000-0000-0000000072d3', '00000000-0000-0000-0000-00000000723b', 'approve_bookings'),
  ('00000000-0000-0000-0000-0000000072d7', '00000000-0000-0000-0000-00000000727b', 'approve_bookings');
insert into public.avisos_negocio_config (tenant_id, tenant_user_id, recibe_avisos, canal_email) values
  ('00000000-0000-0000-0000-0000000072d1', '00000000-0000-0000-0000-00000000721a', false, true), ('00000000-0000-0000-0000-0000000072d1', '00000000-0000-0000-0000-00000000721d', false, true),
  ('00000000-0000-0000-0000-0000000072d2', '00000000-0000-0000-0000-00000000722b', false, true),
  ('00000000-0000-0000-0000-0000000072d3', '00000000-0000-0000-0000-00000000723b', true, true), ('00000000-0000-0000-0000-0000000072d3', '00000000-0000-0000-0000-00000000723a', false, true),
  ('00000000-0000-0000-0000-0000000072d4', '00000000-0000-0000-0000-00000000724b', true, true), ('00000000-0000-0000-0000-0000000072d4', '00000000-0000-0000-0000-00000000724a', false, true);

-- Dice si la transacción tenía tomado el lock del tenant antes y después de ejecutar p_sql (sus efectos, y el lock
-- tomado dentro del bloque, se revierten).
create function pg_temp.lock_por_tenant(p_tenant uuid, p_sql text) returns text
language plpgsql as $$
declare
  v_clave bigint := hashtextextended('avisos_negocio:' || p_tenant::text, 0);
  v_antes boolean;
  v_despues boolean;
begin
  v_antes := exists (select 1 from pg_locks where locktype = 'advisory' and pid = pg_backend_pid()
                      and ((classid::bigint << 32) | objid::bigint) = v_clave);
  begin
    execute p_sql;
    v_despues := exists (select 1 from pg_locks where locktype = 'advisory' and pid = pg_backend_pid()
                          and ((classid::bigint << 32) | objid::bigint) = v_clave);
    raise exception 'revertir' using errcode = 'ZZ001';
  exception when others then
    if sqlstate <> 'ZZ001' then
      return 'err ' || sqlstate || ' ' || sqlerrm;
    end if;
  end;
  return 'antes=' || v_antes || ' despues=' || v_despues;
end
$$;

select is(public.avisos_negocio_destinatarios_email('00000000-0000-0000-0000-0000000072d1'), 1, 'F: su único destinatario es el aprobador 1b');  -- 62
select throws_ok(  -- 63
  $$update public.tenant_users set is_active = false where id = '00000000-0000-0000-0000-00000000721b'$$,
  '23514', 'el negocio no puede quedar sin ningún destinatario de avisos por email', 'desactivar al último destinatario se rechaza');
select throws_ok(  -- 64
  $$delete from public.tenant_user_permissions where tenant_user_id = '00000000-0000-0000-0000-00000000721b' and permission_key = 'approve_bookings'$$,
  '23514', 'el negocio no puede quedar sin ningún destinatario de avisos por email', 'quitarle approve_bookings al último destinatario se rechaza');
select throws_ok(  -- 65
  $$update public.tenant_users set is_owner = false where id = '00000000-0000-0000-0000-00000000722a'$$,
  '23514', 'el negocio no puede quedar sin ningún destinatario de avisos por email', 'quitarle is_owner al último destinatario se rechaza (queda otro owner, con el aviso apagado)');
select lives_ok(  -- 66
  $$update public.tenant_users set is_active = false where id = '00000000-0000-0000-0000-00000000721d'$$,
  'desactivar a quien no es destinatario es válido');
select is(pg_temp.ejecutar('platform_admin', null,  -- 67
  $q$update public.tenant_users set is_active = false where id = '00000000-0000-0000-0000-00000000721b'$q$),
  'err 23514 el negocio no puede quedar sin ningún destinatario de avisos por email', 'platform_admin, que no ve la tabla, recibe el mismo rechazo al desactivar');
select is(public.avisos_negocio_destinatarios_email('00000000-0000-0000-0000-0000000072d1'), 1, 'F conserva su destinatario');  -- 68
select is(pg_temp.escribir('dashboard_service', '00000000-0000-0000-0000-0000000072d3',  -- 69
  $q$delete from public.avisos_negocio_config where tenant_user_id = '00000000-0000-0000-0000-00000000723b'$q$),
  'ok 1', 'borrar la fila de un aprobador lo devuelve al default');
select is(public.avisos_negocio_destinatarios_email('00000000-0000-0000-0000-0000000072d3'), 1, 'H: el aprobador sigue recibiendo por default');  -- 70
select is(pg_temp.escribir('dashboard_service', '00000000-0000-0000-0000-0000000072d3',  -- 71
  $q$delete from public.avisos_negocio_config where tenant_user_id = '00000000-0000-0000-0000-00000000723a'$q$),
  'ok 1', 'borrar la fila del owner también lo devuelve al default');
select is(public.avisos_negocio_destinatarios_email('00000000-0000-0000-0000-0000000072d3'), 2, 'H: ahora reciben el owner y el aprobador por default');  -- 72
select is(pg_temp.ejecutar('dashboard_service', '00000000-0000-0000-0000-0000000072d4',  -- 73
  $q$delete from public.avisos_negocio_config where tenant_user_id = '00000000-0000-0000-0000-00000000724b'$q$),
  'err 23514 el negocio no puede quedar sin ningún destinatario de avisos por email', 'borrar la fila del único destinatario (staff común sin permiso) se rechaza');
select is(public.avisos_negocio_destinatarios_email('00000000-0000-0000-0000-0000000072d4'), 1, 'J conserva su destinatario');  -- 74
select is(pg_temp.lock_por_tenant('00000000-0000-0000-0000-0000000072d5',  -- 75
  $q$insert into public.avisos_negocio_config (tenant_id, tenant_user_id) values ('00000000-0000-0000-0000-0000000072d5', '00000000-0000-0000-0000-00000000725b')$q$),
  'antes=false despues=true', 'cambiar la configuración toma el lock del tenant antes de contar');
select is(pg_temp.lock_por_tenant('00000000-0000-0000-0000-0000000072d6',  -- 76
  $q$update public.tenant_users set is_active = false where id = '00000000-0000-0000-0000-00000000726b'$q$),
  'antes=false despues=true', 'cambiar el staff toma el lock del tenant antes de contar');
select is(pg_temp.lock_por_tenant('00000000-0000-0000-0000-0000000072d7',  -- 77
  $q$delete from public.tenant_user_permissions where tenant_user_id = '00000000-0000-0000-0000-00000000727b'$q$),
  'antes=false despues=true', 'cambiar los permisos toma el lock del tenant antes de contar');
select is(pg_temp.lock_por_tenant('00000000-0000-0000-0000-0000000072d1', 'select 1'),  -- 78
  'antes=true despues=true', 'el lock de un tenant ya tocado sigue tomado en la transacción (la prueba distingue ambos estados)');

-- ---------------------------------------------------------------------------
-- Canal email en notifications_log
-- ---------------------------------------------------------------------------
select ok(  -- 55
  (select pg_get_constraintdef(oid) from pg_constraint
    where conrelid = 'public.notifications_log'::regclass and conname = 'notifications_log_channel_check')
  like '%telegram%whatsapp%email%', 'el CHECK de canal de notifications_log admite email');
select is(pg_temp.escribir('dashboard_service', '00000000-0000-0000-0000-0000000072a1',  -- 56
  $q$select public.record_notification_attempt('00000000-0000-0000-0000-0000000072e1', 'email', 'aviso_negocio', 'sent')$q$),
  'ok 1', 'record_notification_attempt registra un intento por email');
select is(  -- 57
  (select channel || ':' || status from public.notifications_log
    where end_customer_id = '00000000-0000-0000-0000-0000000072e1'),
  'email:sent', 'el intento por email quedó registrado como enviado');
select is(pg_temp.ejecutar('dashboard_service', '00000000-0000-0000-0000-0000000072a1',  -- 58
  $q$select public.record_notification_attempt('00000000-0000-0000-0000-0000000072e1', 'sms', 'aviso_negocio', 'sent')$q$),
  'err 23514 new row for relation "notifications_log" violates check constraint "notifications_log_channel_check"',
  'un canal desconocido se rechaza');
select is(pg_temp.ejecutar('dashboard_service', '00000000-0000-0000-0000-0000000072a1',  -- 59
  $q$select public.record_notification_attempt('00000000-0000-0000-0000-0000000072e1', 'EMAIL', 'aviso_negocio', 'sent')$q$),
  'err 23514 new row for relation "notifications_log" violates check constraint "notifications_log_channel_check"',
  'el canal distingue mayúsculas: EMAIL se rechaza');
select is(pg_temp.escribir('bot_service', '00000000-0000-0000-0000-0000000072a2',  -- 60
  $q$select public.record_notification_attempt('00000000-0000-0000-0000-0000000072e2', 'email', 'aviso_negocio', 'failed', 3, 'timeout')$q$),
  'ok 1', 'bot_service también registra un intento por email (de su tenant)');
-- Visor cross-tenant de platform_admin: mismas columnas de siempre, ahora también con filas de email.
select is(pg_temp.contar('platform_admin', null,  -- 61
  $q$select id, tenant_id, end_customer_id, channel, template, status, retry_count, last_error, created_at, sent_at
       from public.notifications_log where channel = 'email'$q$),
  '2', 'platform_admin ve en el visor las filas de email de ambos tenants con la consulta de siempre');

select * from finish();
rollback;
