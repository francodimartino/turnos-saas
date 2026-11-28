-- Roles web_reservas y enviador: privilegios EXACTOS.
-- Compara, en los dos sentidos, la lista completa de columnas (con su privilegio) y de funciones de cada rol contra
-- la esperada: un privilegio de más o de menos hace fallar el test. Además prueba, con los roles reales, lo que NO
-- pueden hacer (integraciones, Vault, audit_log, usuarios del negocio, secretos, columnas de cobro, created_at de
-- los códigos y las sesiones, borrados) y lo que sí (las escrituras de la web, la devolución de la sesión de un pack).
-- El aislamiento entre negocios con los mismos roles está en turnos_79.
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

-- Ejecuta p_sql como p_rol con el tenant p_ctx ('' = sin contexto). Devuelve 'ok <filas>' o 'err <sqlstate>'.
create function pg_temp.como(p_rol text, p_ctx text, p_sql text) returns text
language plpgsql as $$
declare
  v_filas bigint;
begin
  execute format('set role %I', p_rol);
  perform set_config('app.current_tenant_id', coalesce(p_ctx, ''), true);
  begin
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
-- Lo esperado: privilegio por tabla (columnas = null: todas) y EXECUTE por función
-- ---------------------------------------------------------------------------
create temp table esperado_tabla (rol text, tabla text, priv text, columnas text[]);
insert into esperado_tabla values
  ('web_reservas', 'services', 'SELECT', null),
  ('web_reservas', 'professionals', 'SELECT', null),
  ('web_reservas', 'professional_services', 'SELECT', null),
  ('web_reservas', 'availability_rules', 'SELECT', null),
  ('web_reservas', 'availability_blocks', 'SELECT', null),
  ('web_reservas', 'bookings', 'SELECT', null),
  ('web_reservas', 'bookings', 'INSERT', null),
  ('web_reservas', 'bookings', 'UPDATE', null),
  ('web_reservas', 'tenants', 'SELECT', array['id', 'name', 'settings']),
  ('web_reservas', 'end_customers', 'SELECT',
    array['id', 'tenant_id', 'name', 'phone', 'email', 'created_at', 'deleted_at', 'politica_version',
          'politica_aceptada_at']),
  ('web_reservas', 'end_customers', 'INSERT',
    array['tenant_id', 'name', 'phone', 'email', 'politica_version', 'politica_aceptada_at']),
  ('web_reservas', 'end_customers', 'UPDATE',
    array['name', 'phone', 'email', 'politica_version', 'politica_aceptada_at']),
  ('web_reservas', 'canales_cliente', 'SELECT', null),
  ('web_reservas', 'canales_cliente', 'INSERT',
    array['tenant_id', 'end_customer_id', 'canal', 'identificador', 'verificado_at']),
  ('web_reservas', 'canales_cliente', 'UPDATE', array['verificado_at']),
  ('web_reservas', 'booking_packs', 'SELECT', array['id', 'tenant_id', 'used_sessions']),
  ('web_reservas', 'booking_packs', 'UPDATE', array['used_sessions']),
  ('web_reservas', 'sesiones_web', 'SELECT', null),
  ('web_reservas', 'sesiones_web', 'INSERT', array['tenant_id', 'end_customer_id', 'token_hash', 'expira_at']),
  ('web_reservas', 'sesiones_web', 'UPDATE', array['revocada_at']),
  ('web_reservas', 'codigos_web', 'SELECT', null),
  ('web_reservas', 'codigos_web', 'INSERT', array['tenant_id', 'email_hash', 'codigo_hash', 'expira_at', 'ip_hash']),
  ('web_reservas', 'codigos_web', 'UPDATE', array['intentos', 'usado_at']);
-- enviador: ninguna fila (no tiene privilegio alguno sobre tablas)

create temp table fn_esperado (rol text, fn text);
insert into fn_esperado values
  ('web_reservas', ('public.resolver_slug_tenant(text)'::regprocedure)::text),
  ('web_reservas', ('public.anonymize_end_customer(uuid)'::regprocedure)::text),
  ('web_reservas', ('public.enqueue_notification(uuid, text, text, jsonb, integer)'::regprocedure)::text),
  ('web_reservas', ('public.encolar_aviso_negocio(text, jsonb, integer)'::regprocedure)::text),
  ('enviador', ('public.claim_notification_outbox(integer, integer, integer)'::regprocedure)::text),
  ('enviador', ('public.complete_notification_outbox(uuid, text, text, integer)'::regprocedure)::text),
  ('enviador', ('public.resolve_outbox_delivery(uuid)'::regprocedure)::text),
  ('enviador', ('public.registrar_entrega_outbox(uuid, text, text, text)'::regprocedure)::text),
  ('enviador', ('public.find_integration(text, uuid)'::regprocedure)::text),
  ('enviador', ('public.get_integration_secret(uuid)'::regprocedure)::text),
  ('enviador',
    ('public.record_notification_attempt(uuid, text, text, text, integer, text, uuid)'::regprocedure)::text);

-- Lo esperado, expandido a una fila por columna
create temp view esperado_col as
select e.rol, e.tabla, a.attname::text as col, e.priv
  from esperado_tabla e
  join pg_attribute a
    on a.attrelid = ('public.' || e.tabla)::regclass and a.attnum > 0 and not a.attisdropped
 where e.columnas is null or a.attname::text = any (e.columnas);

-- Lo real: privilegio efectivo sobre cada columna de cada relación de public (el de tabla cuenta en todas)
create temp view real_col as
select r.rol, c.relname::text as tabla, a.attname::text as col, p.priv
  from unnest(array['web_reservas', 'enviador']) as r(rol)
 cross join pg_class c
  join pg_attribute a on a.attrelid = c.oid and a.attnum > 0 and not a.attisdropped
 cross join unnest(array['SELECT', 'INSERT', 'UPDATE']) as p(priv)
 where c.relnamespace = 'public'::regnamespace and c.relkind in ('r', 'p', 'v', 'm', 'f')
   and has_column_privilege(r.rol, c.oid, a.attnum, p.priv);

-- EXECUTE efectivo de cada rol sobre cada función de public: cuenta lo concedido al rol y también lo que le llega por
-- PUBLIC. Las funciones de extensiones quedan fuera (no son del proyecto).
create temp view fn_real as
select r.rol, (p.oid::regprocedure)::text as fn
  from unnest(array['web_reservas', 'enviador']) as r(rol)
 cross join pg_proc p
 where p.pronamespace = 'public'::regnamespace and p.prokind = 'f'
   and not exists (select 1 from pg_depend d where d.objid = p.oid and d.deptype = 'e')
   and has_function_privilege(r.rol, p.oid, 'EXECUTE');

-- ---------------------------------------------------------------------------
-- Datos mínimos (un negocio) para las pruebas con los roles reales
-- ---------------------------------------------------------------------------
insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000078a1', 'A', 'roles-web-a');
insert into public.professionals (id, tenant_id, name) values
  ('00000000-0000-0000-0000-0000000078d1', '00000000-0000-0000-0000-0000000078a1', 'Ana');
insert into public.services (id, tenant_id, name, duration_minutes, price) values
  ('00000000-0000-0000-0000-0000000078e1', '00000000-0000-0000-0000-0000000078a1', 'Corte', 30, 100);
insert into public.end_customers (id, tenant_id, name) values
  ('00000000-0000-0000-0000-0000000078c1', '00000000-0000-0000-0000-0000000078a1', 'Clienta');
insert into public.booking_packs (id, tenant_id, end_customer_id, service_id, total_sessions, used_sessions, origin) values
  ('00000000-0000-0000-0000-0000000078b1', '00000000-0000-0000-0000-0000000078a1',
   '00000000-0000-0000-0000-0000000078c1', '00000000-0000-0000-0000-0000000078e1', 5, 1, 'manual_grant');
insert into public.bookings (id, tenant_id, end_customer_id, service_id, professional_id, time_range, status,
                             booking_pack_id) values
  ('00000000-0000-0000-0000-0000000078f1', '00000000-0000-0000-0000-0000000078a1',
   '00000000-0000-0000-0000-0000000078c1', '00000000-0000-0000-0000-0000000078e1',
   '00000000-0000-0000-0000-0000000078d1', tstzrange('2031-02-03 10:00+00', '2031-02-03 10:30+00'),
   'confirmed', '00000000-0000-0000-0000-0000000078b1');

-- Lo que NO pueden hacer: cada caso tiene que dar 'err 42501' (permiso denegado).
create temp table denegado (rol text, sentencia text, descripcion text);
insert into denegado values
  -- web_reservas: tablas sin ningún privilegio
  ('web_reservas', 'select count(*) from public.tenant_integrations', 'web_reservas no lee tenant_integrations'),
  ('web_reservas', 'select secret_id from public.tenant_integrations', 'web_reservas no lee tenant_integrations.secret_id'),
  ('web_reservas', 'select count(*) from public.audit_log', 'web_reservas no lee audit_log'),
  ('web_reservas', 'select count(*) from public.tenant_users', 'web_reservas no lee tenant_users'),
  ('web_reservas', 'select count(*) from public.tenant_user_permissions', 'web_reservas no lee tenant_user_permissions'),
  ('web_reservas', 'select count(*) from public.notification_outbox', 'web_reservas no lee notification_outbox'),
  ('web_reservas', 'select count(*) from public.notifications_log', 'web_reservas no lee notifications_log'),
  ('web_reservas', 'select count(*) from public.avisos_negocio_config', 'web_reservas no lee avisos_negocio_config'),
  ('web_reservas', 'select count(*) from public.slugs_anteriores', 'web_reservas no lee slugs_anteriores'),
  ('web_reservas', 'select count(*) from public.platform_admins', 'web_reservas no lee platform_admins'),
  ('web_reservas', 'select count(*) from public.payments', 'web_reservas no lee payments'),
  ('web_reservas', 'select count(*) from public.bot_sessions', 'web_reservas no lee bot_sessions'),
  ('web_reservas', 'select count(*) from public.inbound_messages', 'web_reservas no lee inbound_messages'),
  ('web_reservas', 'select count(*) from public.oauth_state_nonces', 'web_reservas no lee oauth_state_nonces'),
  ('web_reservas', 'select count(*) from vault.decrypted_secrets', 'web_reservas no lee vault.decrypted_secrets'),
  ('web_reservas', 'select count(*) from vault.secrets', 'web_reservas no lee vault.secrets'),
  -- web_reservas: funciones sin EXECUTE
  ('web_reservas', 'select public.get_integration_secret(gen_random_uuid())', 'web_reservas no ejecuta get_integration_secret'),
  ('web_reservas', $q$select public.find_integration('telegram_bot')$q$, 'web_reservas no ejecuta find_integration'),
  ('web_reservas', $q$select public.write_audit_log('accion')$q$, 'web_reservas no ejecuta write_audit_log'),
  ('web_reservas', 'select public.podar_codigos_web_vencidos()', 'web_reservas no ejecuta podar_codigos_web_vencidos'),
  ('web_reservas', 'select * from public.claim_notification_outbox()', 'web_reservas no ejecuta claim_notification_outbox'),
  ('web_reservas', 'select * from public.resolve_outbox_delivery(gen_random_uuid())', 'web_reservas no ejecuta resolve_outbox_delivery'),
  ('web_reservas', $q$select public.cambiar_slug_tenant(gen_random_uuid(), 'otro-slug')$q$, 'web_reservas no ejecuta cambiar_slug_tenant'),
  ('web_reservas', $q$select * from public.release_expired_pending_overlaps(gen_random_uuid(), tstzrange(now(), now() + interval '1 hour'))$q$,
    'web_reservas no ejecuta release_expired_pending_overlaps'),
  -- web_reservas: columnas sin privilegio
  ('web_reservas', 'select billing_notes from public.tenants', 'web_reservas no lee tenants.billing_notes'),
  ('web_reservas', 'select status from public.tenants', 'web_reservas no lee tenants.status'),
  ('web_reservas', 'select * from public.tenants', 'web_reservas no lee todas las columnas de tenants'),
  ('web_reservas', 'select channel_user_id from public.end_customers', 'web_reservas no lee end_customers.channel_user_id'),
  ('web_reservas', 'select channel_user_hash from public.end_customers', 'web_reservas no lee end_customers.channel_user_hash'),
  ('web_reservas', 'select extra_data from public.end_customers', 'web_reservas no lee end_customers.extra_data'),
  ('web_reservas', 'select * from public.end_customers', 'web_reservas no lee todas las columnas de end_customers'),
  ('web_reservas', 'select payment_status from public.booking_packs', 'web_reservas no lee booking_packs.payment_status'),
  -- web_reservas: escrituras sin privilegio
  ('web_reservas', $q$update public.tenants set settings = '{}'::jsonb$q$, 'web_reservas no escribe tenants'),
  ('web_reservas', 'update public.end_customers set deleted_at = now()', 'web_reservas no escribe end_customers.deleted_at'),
  ('web_reservas', $q$update public.end_customers set extra_data = '{}'::jsonb$q$, 'web_reservas no escribe end_customers.extra_data'),
  ('web_reservas', $q$update public.end_customers set channel = 'telegram'$q$, 'web_reservas no escribe end_customers.channel'),
  ('web_reservas', 'delete from public.end_customers', 'web_reservas no borra end_customers'),
  ('web_reservas', 'delete from public.bookings', 'web_reservas no borra bookings'),
  ('web_reservas', 'delete from public.canales_cliente', 'web_reservas no borra canales_cliente'),
  ('web_reservas', 'delete from public.sesiones_web', 'web_reservas no borra sesiones_web'),
  ('web_reservas', 'delete from public.codigos_web', 'web_reservas no borra codigos_web'),
  ('web_reservas', $q$update public.canales_cliente set identificador = 'otro'$q$, 'web_reservas solo actualiza canales_cliente.verificado_at'),
  ('web_reservas', $q$update public.sesiones_web set token_hash = repeat('c', 64)$q$, 'web_reservas solo actualiza sesiones_web.revocada_at'),
  ('web_reservas', 'update public.sesiones_web set expira_at = now()', 'web_reservas no cambia sesiones_web.expira_at'),
  ('web_reservas', 'update public.codigos_web set expira_at = now()', 'web_reservas no cambia codigos_web.expira_at'),
  ('web_reservas', $q$update public.codigos_web set codigo_hash = repeat('c', 64)$q$, 'web_reservas no cambia codigos_web.codigo_hash'),
  ('web_reservas', 'update public.booking_packs set total_sessions = 99', 'web_reservas solo actualiza booking_packs.used_sessions'),
  ('web_reservas', $q$insert into public.codigos_web (tenant_id, email_hash, codigo_hash, expira_at, created_at)
      values ('00000000-0000-0000-0000-0000000078a1', repeat('a', 64), repeat('b', 64), now() + interval '5 minutes', now())$q$,
    'web_reservas no inserta codigos_web.created_at'),
  ('web_reservas', $q$insert into public.codigos_web (tenant_id, email_hash, codigo_hash, expira_at, intentos)
      values ('00000000-0000-0000-0000-0000000078a1', repeat('a', 64), repeat('b', 64), now() + interval '5 minutes', 0)$q$,
    'web_reservas no inserta codigos_web.intentos'),
  ('web_reservas', $q$insert into public.sesiones_web (tenant_id, end_customer_id, token_hash, expira_at, created_at)
      values ('00000000-0000-0000-0000-0000000078a1', '00000000-0000-0000-0000-0000000078c1', repeat('a', 64),
              now() + interval '1 day', now())$q$,
    'web_reservas no inserta sesiones_web.created_at'),
  ('web_reservas', $q$insert into public.end_customers (tenant_id, channel, channel_user_id)
      values ('00000000-0000-0000-0000-0000000078a1', 'telegram', 'x')$q$,
    'web_reservas no inserta end_customers.channel'),
  ('web_reservas', $q$insert into public.canales_cliente (tenant_id, end_customer_id, canal, identificador, created_at)
      values ('00000000-0000-0000-0000-0000000078a1', '00000000-0000-0000-0000-0000000078c1', 'email', 'x@a.test', now())$q$,
    'web_reservas no inserta canales_cliente.created_at'),
  ('web_reservas', $q$insert into public.booking_packs (tenant_id, end_customer_id, service_id, total_sessions, origin)
      values ('00000000-0000-0000-0000-0000000078a1', '00000000-0000-0000-0000-0000000078c1',
              '00000000-0000-0000-0000-0000000078e1', 3, 'manual_grant')$q$,
    'web_reservas no crea booking_packs'),
  ('web_reservas', $q$insert into public.services (tenant_id, name, duration_minutes, price)
      values ('00000000-0000-0000-0000-0000000078a1', 'Nuevo', 30, 1)$q$, 'web_reservas no escribe services'),
  ('web_reservas', $q$update public.professionals set name = 'otro'$q$, 'web_reservas no escribe professionals'),
  -- enviador: ni lee ni escribe tablas de dominio ni de plataforma
  ('enviador', 'select count(*) from public.bookings', 'enviador no lee bookings'),
  ('enviador', 'select count(*) from public.end_customers', 'enviador no lee end_customers'),
  ('enviador', 'select count(*) from public.services', 'enviador no lee services'),
  ('enviador', 'select count(*) from public.professionals', 'enviador no lee professionals'),
  ('enviador', 'select count(*) from public.canales_cliente', 'enviador no lee canales_cliente'),
  ('enviador', 'select count(*) from public.sesiones_web', 'enviador no lee sesiones_web'),
  ('enviador', 'select count(*) from public.codigos_web', 'enviador no lee codigos_web'),
  ('enviador', 'select id from public.tenants', 'enviador no lee tenants'),
  ('enviador', 'select count(*) from public.tenant_integrations', 'enviador no lee tenant_integrations'),
  ('enviador', 'select count(*) from public.notification_outbox', 'enviador no lee notification_outbox'),
  ('enviador', 'select count(*) from public.notifications_log', 'enviador no lee notifications_log'),
  ('enviador', 'select count(*) from public.audit_log', 'enviador no lee audit_log'),
  ('enviador', 'select count(*) from public.tenant_users', 'enviador no lee tenant_users'),
  ('enviador', 'select count(*) from public.avisos_negocio_config', 'enviador no lee avisos_negocio_config'),
  ('enviador', 'select count(*) from vault.decrypted_secrets', 'enviador no lee vault.decrypted_secrets'),
  ('enviador', $q$update public.bookings set status = 'cancelled'$q$, 'enviador no escribe bookings'),
  ('enviador', $q$update public.end_customers set name = 'x'$q$, 'enviador no escribe end_customers'),
  ('enviador', $q$update public.notification_outbox set status = 'sent'$q$, 'enviador no escribe notification_outbox'),
  ('enviador', 'delete from public.bookings', 'enviador no borra bookings'),
  ('enviador', 'delete from public.notification_outbox', 'enviador no borra notification_outbox'),
  ('enviador', $q$insert into public.end_customers (tenant_id, name)
      values ('00000000-0000-0000-0000-0000000078a1', 'x')$q$, 'enviador no inserta end_customers'),
  ('enviador', $q$insert into public.notifications_log (tenant_id, end_customer_id, channel, template, status)
      values ('00000000-0000-0000-0000-0000000078a1', '00000000-0000-0000-0000-0000000078c1', 'telegram', 'x', 'sent')$q$,
    'enviador no inserta notifications_log (solo por record_notification_attempt)'),
  -- enviador: funciones sin EXECUTE
  ('enviador', $q$select public.enqueue_notification(gen_random_uuid(), 'telegram', 'turno_confirmado')$q$, 'enviador no ejecuta enqueue_notification'),
  ('enviador', $q$select public.encolar_aviso_negocio('aviso_negocio_reserva', '{}'::jsonb)$q$, 'enviador no ejecuta encolar_aviso_negocio'),
  ('enviador', 'select * from public.reclamar_avisos_telegram(10, 300, 5)', 'enviador no ejecuta reclamar_avisos_telegram'),
  ('enviador', 'select public.prune_notification_outbox()', 'enviador no ejecuta prune_notification_outbox'),
  ('enviador', $q$select * from public.resolver_slug_tenant('roles-web-a')$q$, 'enviador no ejecuta resolver_slug_tenant'),
  ('enviador', 'select public.anonymize_end_customer(gen_random_uuid())', 'enviador no ejecuta anonymize_end_customer'),
  ('enviador', $q$select public.write_audit_log('accion')$q$, 'enviador no ejecuta write_audit_log'),
  ('enviador', 'select public.podar_codigos_web_vencidos()', 'enviador no ejecuta podar_codigos_web_vencidos');

select plan(32 + (select count(*)::int from denegado));

-- ---------------------------------------------------------------------------
-- Atributos de los roles
-- ---------------------------------------------------------------------------
select is(
  (select count(*) from pg_roles
    where rolname in ('web_reservas', 'enviador')
      and rolcanlogin and not rolsuper and not rolcreatedb and not rolcreaterole and not rolreplication
      and not rolbypassrls and not rolinherit),
  2::bigint, 'web_reservas y enviador: LOGIN, sin superuser, sin CREATEDB/CREATEROLE/REPLICATION, sin BYPASSRLS y sin herencia');  -- 1
select is_empty(
  $$select r.rolname, g.rolname from pg_auth_members m
      join pg_roles r on r.oid = m.member join pg_roles g on g.oid = m.roleid
     where r.rolname in ('web_reservas', 'enviador')$$,
  'ninguno de los dos roles es miembro de otro rol');  -- 2
select is_empty(
  $$select c.relname from pg_class c
     where c.relowner in (select oid from pg_roles where rolname in ('web_reservas', 'enviador'))$$,
  'ninguno de los dos roles es dueño de relaciones');  -- 3
select is_empty(
  $$select p.proname from pg_proc p
     where p.proowner in (select oid from pg_roles where rolname in ('web_reservas', 'enviador'))$$,
  'ninguno de los dos roles es dueño de funciones');  -- 4
select ok(has_schema_privilege('web_reservas', 'public', 'USAGE') and has_schema_privilege('enviador', 'public', 'USAGE')
          and not has_schema_privilege('web_reservas', 'public', 'CREATE')
          and not has_schema_privilege('enviador', 'public', 'CREATE'),
  'usan el esquema public y no pueden crear objetos en él');  -- 5
select ok(not has_schema_privilege('web_reservas', 'vault', 'USAGE') and not has_schema_privilege('enviador', 'vault', 'USAGE')
          and not has_schema_privilege('web_reservas', 'auth', 'USAGE') and not has_schema_privilege('enviador', 'auth', 'USAGE'),
  'ninguno de los dos tiene USAGE sobre los esquemas vault y auth');  -- 6

-- ---------------------------------------------------------------------------
-- Tablas y columnas exactas
-- ---------------------------------------------------------------------------
select isnt_empty($$select 1 from esperado_col$$, 'la lista esperada de columnas no está vacía');  -- 7
select is_empty($$select rol, tabla, col, priv from real_col except select rol, tabla, col, priv from esperado_col$$,
  'ningún rol tiene un privilegio de columna que no figure en la lista exacta');  -- 8
select is_empty($$select rol, tabla, col, priv from esperado_col except select rol, tabla, col, priv from real_col$$,
  'cada privilegio de columna de la lista exacta está concedido');  -- 9
select is((select count(*) from esperado_col where rol = 'web_reservas'),
          (select count(*) from real_col where rol = 'web_reservas'),
  'web_reservas: la cantidad de privilegios de columna coincide con la lista');  -- 10
select is((select count(*) from real_col where rol = 'enviador'), 0::bigint,
  'enviador no tiene ningún privilegio de columna ni de tabla');  -- 11
select is_empty(
  $$select r.rol, c.relname
      from unnest(array['web_reservas', 'enviador']) as r(rol)
     cross join pg_class c
     where c.relnamespace = 'public'::regnamespace and c.relkind in ('r', 'p', 'v', 'm', 'f')
       and (has_table_privilege(r.rol, c.oid, 'DELETE,TRUNCATE,TRIGGER')
            or has_any_column_privilege(r.rol, c.oid, 'REFERENCES'))$$,
  'ninguno de los dos roles tiene DELETE, TRUNCATE, TRIGGER ni REFERENCES sobre ninguna relación de public');  -- 12
select is_empty(
  $$select r.rol, c.relname
      from unnest(array['web_reservas', 'enviador']) as r(rol)
     cross join pg_class c
     where c.relnamespace = 'public'::regnamespace and c.relkind = 'S'
       and has_sequence_privilege(r.rol, c.oid, 'USAGE,SELECT,UPDATE')$$,
  'ninguno de los dos roles tiene privilegios sobre secuencias de public');  -- 13
select is(
  (select string_agg(a.attname::text, ',' order by a.attnum) from pg_attribute a
    where a.attrelid = 'public.codigos_web'::regclass and a.attnum > 0 and not a.attisdropped
      and has_column_privilege('web_reservas', a.attrelid, a.attnum, 'INSERT')),
  'tenant_id,email_hash,codigo_hash,expira_at,ip_hash',
  'web_reservas inserta codigos_web sin created_at, intentos ni usado_at');  -- 14

-- ---------------------------------------------------------------------------
-- Funciones exactas
-- ---------------------------------------------------------------------------
select is_empty($$select rol, fn from fn_real except select rol, fn from fn_esperado$$,
  'ningún rol puede ejecutar (por concesión directa o por PUBLIC) una función que no figure en la lista exacta');  -- 15
select is_empty($$select rol, fn from fn_esperado except select rol, fn from fn_real$$,
  'cada EXECUTE de la lista exacta de funciones está concedido');  -- 16
select is((select count(*) from fn_real), (select count(*) from fn_esperado),
  'la cantidad de funciones con EXECUTE coincide con la lista');  -- 17
select is_empty(
  $$select p.proname from pg_proc p
     where p.pronamespace = 'public'::regnamespace and p.prosecdef
       and (has_function_privilege('anon', p.oid, 'EXECUTE') or has_function_privilege('authenticated', p.oid, 'EXECUTE')
            or has_function_privilege('service_role', p.oid, 'EXECUTE'))$$,
  'ninguna función SECURITY DEFINER es ejecutable por anon, authenticated ni service_role');  -- 18

-- ---------------------------------------------------------------------------
-- Lo que no pueden hacer (con los roles reales): una prueba por cada fila de la tabla denegado
-- ---------------------------------------------------------------------------
select is(pg_temp.como(d.rol, '00000000-0000-0000-0000-0000000078a1', d.sentencia), 'err 42501', d.descripcion)
  from denegado d order by d.rol, d.descripcion;

-- ---------------------------------------------------------------------------
-- Lo que sí pueden hacer
-- ---------------------------------------------------------------------------
select is(pg_temp.como('web_reservas', '00000000-0000-0000-0000-0000000078a1',
  $q$insert into public.codigos_web (tenant_id, email_hash, codigo_hash, expira_at, ip_hash)
     values ('00000000-0000-0000-0000-0000000078a1', repeat('a', 64), repeat('b', 64), now() + interval '5 minutes',
             repeat('c', 64))$q$), 'ok 1', 'web_reservas inserta un código (sin created_at)');  -- 19
select is(pg_temp.como('web_reservas', '00000000-0000-0000-0000-0000000078a1',
  $q$update public.codigos_web set intentos = 1, usado_at = now() where email_hash = repeat('a', 64)$q$),
  'ok 1', 'web_reservas actualiza intentos y usado_at de un código');  -- 20
select is(pg_temp.como('web_reservas', '00000000-0000-0000-0000-0000000078a1',
  $q$insert into public.sesiones_web (tenant_id, end_customer_id, token_hash, expira_at)
     values ('00000000-0000-0000-0000-0000000078a1', '00000000-0000-0000-0000-0000000078c1', repeat('d', 64),
             now() + interval '1 day')$q$), 'ok 1', 'web_reservas inserta una sesión (sin created_at)');  -- 21
select is(pg_temp.como('web_reservas', '00000000-0000-0000-0000-0000000078a1',
  $q$update public.sesiones_web set revocada_at = now() where token_hash = repeat('d', 64)$q$),
  'ok 1', 'web_reservas revoca una sesión');  -- 22
select is(pg_temp.como('web_reservas', '00000000-0000-0000-0000-0000000078a1',
  $q$insert into public.end_customers (tenant_id, name, phone, email, politica_version, politica_aceptada_at)
     values ('00000000-0000-0000-0000-0000000078a1', 'Nueva', '+5491100007801', 'nueva@a.test', 'v1', now())$q$),
  'ok 1', 'web_reservas crea una ficha con la política aceptada');  -- 23
select is(pg_temp.como('web_reservas', '00000000-0000-0000-0000-0000000078a1',
  $q$insert into public.canales_cliente (tenant_id, end_customer_id, canal, identificador, verificado_at)
     values ('00000000-0000-0000-0000-0000000078a1', '00000000-0000-0000-0000-0000000078c1', 'email', 'clienta@a.test',
             now())$q$), 'ok 1', 'web_reservas registra el canal email verificado');  -- 24
select is(pg_temp.como('web_reservas', '00000000-0000-0000-0000-0000000078a1',
  $q$insert into public.bookings (tenant_id, end_customer_id, service_id, professional_id, time_range, status)
     values ('00000000-0000-0000-0000-0000000078a1', '00000000-0000-0000-0000-0000000078c1',
             '00000000-0000-0000-0000-0000000078e1', '00000000-0000-0000-0000-0000000078d1',
             tstzrange('2031-02-04 10:00+00', '2031-02-04 10:30+00'), 'confirmed')$q$),
  'ok 1', 'web_reservas reserva un turno');  -- 25
select is(pg_temp.como('web_reservas', '',
  $q$select * from public.resolver_slug_tenant('roles-web-a')$q$), 'ok 1',
  'web_reservas resuelve el slug de un negocio activo, sin contexto de tenant');  -- 26
select is(pg_temp.como('web_reservas', '00000000-0000-0000-0000-0000000078a1',
  $q$select id, name, settings from public.tenants$q$), 'ok 1', 'web_reservas lee id, nombre y settings de su negocio');  -- 27

-- Cancelar un turno que consumió una sesión de un pack: el trigger de bookings la devuelve con los privilegios de
-- quien cancela
select is(pg_temp.como('web_reservas', '00000000-0000-0000-0000-0000000078a1',
  $q$update public.bookings set status = 'cancelled' where id = '00000000-0000-0000-0000-0000000078f1'$q$),
  'ok 1', 'web_reservas cancela un turno que consumió una sesión de pack');  -- 28
select is((select used_sessions from public.booking_packs where id = '00000000-0000-0000-0000-0000000078b1'), 0,
  'la cancelación devolvió la sesión del pack');  -- 29

-- El enviador ejecuta sus funciones (con contexto de tenant): sin filas que resolver, no devuelve nada
select is(pg_temp.como('enviador', '00000000-0000-0000-0000-0000000078a1',
  $q$select * from public.resolve_outbox_delivery(gen_random_uuid())$q$), 'ok 0',
  'enviador ejecuta resolve_outbox_delivery');  -- 30

-- El meta-test detecta lo malo: un privilegio de más hace fallar la comparación exacta (se revierte con el rollback)
grant select on public.audit_log to web_reservas;
grant execute on function public.get_integration_secret(uuid) to web_reservas;
select isnt_empty(
  $$select rol, tabla, col, priv from real_col except select rol, tabla, col, priv from esperado_col$$,
  'detecta un privilegio de tabla de más (audit_log)');  -- 31
select isnt_empty($$select rol, fn from fn_real except select rol, fn from fn_esperado$$,
  'detecta un EXECUTE de más (get_integration_secret)');  -- 32

select * from finish();
rollback;
