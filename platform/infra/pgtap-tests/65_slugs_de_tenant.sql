-- Slugs de tenant: resolver_slug_tenant (solo activos), historial slugs_anteriores, slugs reservados y
-- cambiar_slug_tenant (solo platform_admin, con actor y audit_log) (§2.12).
begin;
select plan(59);

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

-- Ejecuta una consulta como un rol, con el actor dado ('' = sin actor). Devuelve el texto o 'err <sqlstate>'.
create function pg_temp.como(p_rol text, p_actor text, p_sql text) returns text
language plpgsql as $$
declare
  v text;
begin
  execute format('set role %I', p_rol);
  perform set_config('app.current_actor_id', coalesce(p_actor, ''), true);
  begin
    execute p_sql into v;
    reset role;
    return coalesce(v, '<null>');
  exception when others then
    reset role;
    return 'err ' || sqlstate;
  end;
end
$$;

insert into public.tenants (id, name, slug, status, deactivated_at) values
  ('00000000-0000-0000-0000-0000000065a1', 'Activo A', 'slug-activo-a', 'active', null),
  ('00000000-0000-0000-0000-0000000065a2', 'Activo B', 'slug-activo-b', 'active', null),
  ('00000000-0000-0000-0000-0000000065a3', 'Suspendido', 'slug-suspendido', 'suspended', null),
  ('00000000-0000-0000-0000-0000000065a4', 'Inactivo', 'slug-inactivo', 'inactive', now()),
  ('00000000-0000-0000-0000-0000000065a5', 'Purgando', 'slug-purgando', 'purging', now()),
  ('00000000-0000-0000-0000-0000000065a6', 'Purgado', 'slug-purgado', 'purged', null);

-- Slugs anteriores vigentes de tenants que no están activos (y uno de un activo, para el aislamiento)
insert into public.slugs_anteriores (slug, tenant_id, reemplazado_at, vigente_hasta) values
  ('viejo-suspendido', '00000000-0000-0000-0000-0000000065a3', now(), now() + interval '30 days'),
  ('viejo-inactivo', '00000000-0000-0000-0000-0000000065a4', now(), now() + interval '30 days'),
  ('viejo-purgando', '00000000-0000-0000-0000-0000000065a5', now(), now() + interval '30 days'),
  ('viejo-purgado', '00000000-0000-0000-0000-0000000065a6', now(), now() + interval '30 days'),
  ('viejo-de-b', '00000000-0000-0000-0000-0000000065a2', now(), now() + interval '30 days'),
  ('viejo-de-a', '00000000-0000-0000-0000-0000000065a1', now(), now() + interval '30 days');

-- Estructura y grants
select has_table('public', 'slugs_anteriores', 'existe slugs_anteriores');  -- 1
select ok((select relrowsecurity from pg_class where oid = 'public.slugs_anteriores'::regclass),  -- 2
  'slugs_anteriores tiene RLS activada');
select ok(not exists (  -- 3
  select 1 from unnest(array['bot_service', 'dashboard_service', 'platform_admin', 'platform_admin_readonly',
                             'purge_job', 'scheduler_job', 'anon', 'authenticated', 'service_role']) r
   where has_any_column_privilege(r, 'public.slugs_anteriores', 'SELECT,INSERT,UPDATE,REFERENCES')
      or has_table_privilege(r, 'public.slugs_anteriores', 'DELETE,TRUNCATE,TRIGGER')),
  'ningun rol de aplicacion tiene privilegios directos sobre slugs_anteriores');
select ok(not exists (  -- 4
  select 1 from unnest(array['bot_service', 'dashboard_service', 'platform_admin', 'platform_admin_readonly',
                             'purge_job', 'scheduler_job', 'anon', 'authenticated', 'service_role']) r
   where has_function_privilege(r, 'public.resolver_slug_tenant(text)', 'execute')),
  'resolver_slug_tenant no la ejecuta ningun rol de aplicacion (el rol de la web la recibe con su alta)');
select ok(  -- 5
  has_function_privilege('platform_admin', 'public.cambiar_slug_tenant(uuid, text)', 'execute')
  and not exists (
    select 1 from unnest(array['bot_service', 'dashboard_service', 'platform_admin_readonly', 'purge_job',
                               'scheduler_job', 'anon', 'authenticated', 'service_role']) r
     where has_function_privilege(r, 'public.cambiar_slug_tenant(uuid, text)', 'execute')),
  'cambiar_slug_tenant: solo platform_admin');
select ok(not has_function_privilege('platform_admin', 'public.slug_reservado(text)', 'execute'),  -- 6
  'slug_reservado es interna: ningun rol de aplicacion la ejecuta');

-- Resolución: solo tenants activos
select is((select tenant_id::text || '/' || nombre || '/' || slug_vigente || '/' || reemplazado::text  -- 7
             from public.resolver_slug_tenant('slug-activo-a')),
  '00000000-0000-0000-0000-0000000065a1/Activo A/slug-activo-a/false', 'un tenant activo resuelve');
select is((select count(*) from public.resolver_slug_tenant('slug-suspendido')), 0::bigint,  -- 8
  'un tenant suspendido no resuelve');
select is((select count(*) from public.resolver_slug_tenant('slug-inactivo')), 0::bigint,  -- 9
  'un tenant inactivo no resuelve');
select is((select count(*) from public.resolver_slug_tenant('slug-purgando')), 0::bigint,  -- 10
  'un tenant en purga no resuelve');
select is((select count(*) from public.resolver_slug_tenant('slug-purgado')), 0::bigint,  -- 11
  'un tenant purgado no resuelve');
select is((select count(*) from public.resolver_slug_tenant('no-existe')), 0::bigint,  -- 12
  'un slug inexistente no resuelve');
select is((select count(*) from public.resolver_slug_tenant(null)), 0::bigint, 'un slug nulo no resuelve');  -- 13
select is((select count(*) from public.resolver_slug_tenant('slug-activo-a')  -- 14
            where tenant_id <> '00000000-0000-0000-0000-0000000065a1'),
  0::bigint, 'la resolucion no devuelve datos de otro tenant');

-- Slugs reservados: create_tenant (insert en tenants) los rechaza en la base
select throws_ok($$insert into public.tenants (name, slug) values ('X', 'precios')$$, '23514',  -- 15
  'slug reservado: precios', 'el alta con un slug reservado falla con un error claro');
select throws_ok($$insert into public.tenants (name, slug) values ('X', 'reservas-api')$$, '23514', null,  -- 16
  'tambien reservas-api');
select is(pg_temp.como('platform_admin', '00000000-0000-0000-0000-00000000aaaa',  -- 17
  $$insert into public.tenants (name, slug) values ('X', 'login')$$), 'err 23514',
  'platform_admin tampoco puede dar de alta un slug reservado');

-- Cambio de slug: permisos y validaciones
select is(pg_temp.como('bot_service', '00000000-0000-0000-0000-00000000aaaa',  -- 18
  $$select public.cambiar_slug_tenant('00000000-0000-0000-0000-0000000065a1', 'slug-nuevo-a')$$),
  'err 42501', 'un rol que no es platform_admin no puede cambiar el slug');
select is(pg_temp.como('platform_admin', '',  -- 19
  $$select public.cambiar_slug_tenant('00000000-0000-0000-0000-0000000065a1', 'slug-nuevo-a')$$),
  'err 42501', 'sin actor verificado se rechaza');
select is(pg_temp.como('platform_admin', '00000000-0000-0000-0000-00000000aaaa',  -- 20
  $$select public.cambiar_slug_tenant('00000000-0000-0000-0000-0000000065a1', 'admin')$$),
  'err 23514', 'rechaza un slug reservado');
select is(pg_temp.como('platform_admin', '00000000-0000-0000-0000-00000000aaaa',  -- 21
  $$select public.cambiar_slug_tenant('00000000-0000-0000-0000-0000000065a1', 'Con_Mayusculas')$$),
  'err 22023', 'rechaza un formato invalido');
select is(pg_temp.como('platform_admin', '00000000-0000-0000-0000-00000000aaaa',  -- 22
  $$select public.cambiar_slug_tenant('00000000-0000-0000-0000-0000000065a1', 'ab')$$),
  'err 22023', 'rechaza un slug demasiado corto');
select is(pg_temp.como('platform_admin', '00000000-0000-0000-0000-00000000aaaa',  -- 23
  $$select public.cambiar_slug_tenant('00000000-0000-0000-0000-0000000065a1', 'slug-activo-b')$$),
  'err 23505', 'rechaza el slug de otro tenant');
select is(pg_temp.como('platform_admin', '00000000-0000-0000-0000-00000000aaaa',  -- 24
  $$select public.cambiar_slug_tenant('00000000-0000-0000-0000-0000000065a5', 'slug-nuevo-p')$$),
  'err 23514', 'un tenant en purga no cambia de slug');

-- Cambio exitoso: el viejo redirige al nuevo, con historial y audit_log del actor
select is(pg_temp.como('platform_admin', '00000000-0000-0000-0000-00000000aaaa',  -- 25
  $$select public.cambiar_slug_tenant('00000000-0000-0000-0000-0000000065a1', 'slug-nuevo-a')$$),
  'slug-nuevo-a', 'platform_admin cambia el slug');
select is((select slug from public.tenants where id = '00000000-0000-0000-0000-0000000065a1'),  -- 26
  'slug-nuevo-a', 'el tenant tiene el slug nuevo');
select is((select tenant_id::text || '/' || slug_vigente || '/' || reemplazado::text  -- 27
             from public.resolver_slug_tenant('slug-activo-a')),
  '00000000-0000-0000-0000-0000000065a1/slug-nuevo-a/true', 'el link viejo resuelve al nuevo, marcado como reemplazado');
select ok((select vigente_hasta between now() + interval '179 days' and now() + interval '181 days'  -- 28
             from public.slugs_anteriores where slug = 'slug-activo-a'),
  'la redireccion dura 180 dias');
select is((select actor_id::text || '/' || (metadata ->> 'slug_anterior') || '/' || (metadata ->> 'slug_nuevo')  -- 29
             from public.audit_log
            where action = 'tenant_slug_change' and tenant_id = '00000000-0000-0000-0000-0000000065a1'),
  '00000000-0000-0000-0000-00000000aaaa/slug-activo-a/slug-nuevo-a', 'audit_log guarda el actor y los dos slugs');

-- Unicidad contra slugs anteriores vigentes
select is(pg_temp.como('platform_admin', '00000000-0000-0000-0000-00000000aaaa',  -- 30
  $$select public.cambiar_slug_tenant('00000000-0000-0000-0000-0000000065a2', 'slug-activo-a')$$),
  'err 23505', 'otro tenant no puede tomar un slug anterior aun vigente');
select throws_ok($$insert into public.tenants (name, slug) values ('X', 'slug-activo-a')$$, '23505', null,  -- 31
  'el alta tampoco puede tomarlo');

-- Vencida la ventana: el link viejo ya no resuelve y el slug queda libre
update public.slugs_anteriores
   set reemplazado_at = now() - interval '200 days', vigente_hasta = now() - interval '20 days'
 where slug = 'slug-activo-a';
select is((select count(*) from public.resolver_slug_tenant('slug-activo-a')), 0::bigint,  -- 32
  'vencida la ventana, el link viejo no resuelve');
select is(pg_temp.como('platform_admin', '00000000-0000-0000-0000-00000000aaaa',  -- 33
  $$select public.cambiar_slug_tenant('00000000-0000-0000-0000-0000000065a2', 'slug-activo-a')$$),
  'slug-activo-a', 'vencida la ventana, otro tenant puede tomar el slug');

-- Mismo slug vigente, y el slug que dejó el segundo tenant
select is(pg_temp.como('platform_admin', '00000000-0000-0000-0000-00000000aaaa',  -- 34
  $$select public.cambiar_slug_tenant('00000000-0000-0000-0000-0000000065a1', 'slug-nuevo-a')$$),
  'err 22023', 'cambiar al mismo slug vigente se rechaza');
select is((select count(*) from public.resolver_slug_tenant('slug-activo-b')  -- 35
            where tenant_id = '00000000-0000-0000-0000-0000000065a2' and reemplazado),
  1::bigint, 'el slug que dejo el segundo tenant redirige a su slug nuevo');

-- Redirección: un slug anterior vigente de un tenant no activo no resuelve
select is((select count(*) from public.resolver_slug_tenant('viejo-suspendido')), 0::bigint,  -- 36
  'el slug anterior de un tenant suspendido no redirige');
select is((select count(*) from public.resolver_slug_tenant('viejo-inactivo')), 0::bigint,  -- 37
  'el slug anterior de un tenant inactivo no redirige');
select is((select count(*) from public.resolver_slug_tenant('viejo-purgando')), 0::bigint,  -- 38
  'el slug anterior de un tenant en purga no redirige');
select is((select count(*) from public.resolver_slug_tenant('viejo-purgado')), 0::bigint,  -- 39
  'el slug anterior de un tenant purgado no redirige');

-- Aislamiento RLS de slugs_anteriores (grant transitorio: el rollback final lo revierte)
grant select on public.slugs_anteriores to dashboard_service;
select is(pg_temp.como('dashboard_service', '',  -- 40
  $$select count(*)::text from public.slugs_anteriores$$), '0', 'sin contexto de tenant no se ve ninguna fila');
create function pg_temp.con_contexto(p_ctx text, p_sql text) returns text
language plpgsql as $$
declare
  v text;
begin
  execute 'set role dashboard_service';
  perform set_config('app.current_tenant_id', p_ctx, true);
  execute p_sql into v;
  reset role;
  perform set_config('app.current_tenant_id', '', true);
  return v;
end
$$;
select is(pg_temp.con_contexto('00000000-0000-0000-0000-0000000065a1',  -- 41
  $$select string_agg(slug, ',' order by slug) from public.slugs_anteriores$$),
  'viejo-de-a', 'con el contexto de A se ven solo las filas de A');
select is(pg_temp.con_contexto('00000000-0000-0000-0000-0000000065a1',  -- 42
  $$select count(*)::text from public.slugs_anteriores where tenant_id = '00000000-0000-0000-0000-0000000065a2'$$),
  '0', 'con el contexto de A no se ve ninguna fila de B');
select is(pg_temp.con_contexto('00000000-0000-0000-0000-0000000065a2',  -- 43
  $$select string_agg(slug, ',' order by slug) from public.slugs_anteriores$$),
  'slug-activo-b,viejo-de-b', 'con el contexto de B se ven solo las filas de B');

-- El slug solo cambia por la funcion
select is(pg_temp.como('platform_admin', '00000000-0000-0000-0000-00000000aaaa',  -- 44
  $$update public.tenants set slug = 'directo-sin-historial' where id = '00000000-0000-0000-0000-0000000065a2'$$),
  'err 42501', 'platform_admin no puede cambiar el slug con un UPDATE directo');

-- La purga borra los slugs anteriores del tenant
update public.tenants set status = 'purging' where id = '00000000-0000-0000-0000-0000000065a4';
update public.tenants set status = 'purged', slug = 'slug-inactivo-purged-20260101'
 where id = '00000000-0000-0000-0000-0000000065a4';
select is((select count(*) from public.slugs_anteriores where tenant_id = '00000000-0000-0000-0000-0000000065a4'),  -- 45
  0::bigint, 'al purgar el tenant se borran sus slugs anteriores');

-- Largo del slug: bordes 3 y 40 aceptados, 41 rechazado
insert into public.tenants (id, name, slug) values
  ('00000000-0000-0000-0000-0000000065c1', 'Largo', 'largo-original'),
  ('00000000-0000-0000-0000-0000000065d1', 'Vuelta', 'vuelta-a');
select is(pg_temp.como('platform_admin', '00000000-0000-0000-0000-00000000aaaa',  -- 46
  $$select public.cambiar_slug_tenant('00000000-0000-0000-0000-0000000065c1', repeat('x', 40))$$),
  repeat('x', 40), 'un slug de 40 caracteres se acepta');
select is(pg_temp.como('platform_admin', '00000000-0000-0000-0000-00000000aaaa',  -- 47
  $$select public.cambiar_slug_tenant('00000000-0000-0000-0000-0000000065c1', repeat('y', 41))$$),
  'err 22023', 'un slug de 41 caracteres se rechaza');
select is(pg_temp.como('platform_admin', '00000000-0000-0000-0000-00000000aaaa',  -- 48
  $$select public.cambiar_slug_tenant('00000000-0000-0000-0000-0000000065c1', 'abc')$$),
  'abc', 'un slug de 3 caracteres se acepta');

-- Guiones mal puestos, nulo, tenant inexistente y tenant purgado
select is(pg_temp.como('platform_admin', '00000000-0000-0000-0000-00000000aaaa',  -- 49
  $$select public.cambiar_slug_tenant('00000000-0000-0000-0000-0000000065c1', '-abc')$$),
  'err 22023', 'rechaza un guion al principio');
select is(pg_temp.como('platform_admin', '00000000-0000-0000-0000-00000000aaaa',  -- 50
  $$select public.cambiar_slug_tenant('00000000-0000-0000-0000-0000000065c1', 'abc-')$$),
  'err 22023', 'rechaza un guion al final');
select is(pg_temp.como('platform_admin', '00000000-0000-0000-0000-00000000aaaa',  -- 51
  $$select public.cambiar_slug_tenant('00000000-0000-0000-0000-0000000065c1', 'a--b')$$),
  'err 22023', 'rechaza guiones seguidos');
select is(pg_temp.como('platform_admin', '00000000-0000-0000-0000-00000000aaaa',  -- 52
  $$select public.cambiar_slug_tenant('00000000-0000-0000-0000-0000000065c1', null)$$),
  'err 22023', 'rechaza un slug nulo');
select is(pg_temp.como('platform_admin', '00000000-0000-0000-0000-00000000aaaa',  -- 53
  $$select public.cambiar_slug_tenant('00000000-0000-0000-0000-00000000dead', 'slug-valido-x')$$),
  'err P0002', 'un tenant inexistente da no_data_found');
select is(pg_temp.como('platform_admin', '00000000-0000-0000-0000-00000000aaaa',  -- 54
  $$select public.cambiar_slug_tenant('00000000-0000-0000-0000-0000000065a6', 'slug-valido-y')$$),
  'err 23514', 'un tenant purgado no cambia de slug');

-- Volver a un slug anterior propio vigente (A -> B -> A)
select is(pg_temp.como('platform_admin', '00000000-0000-0000-0000-00000000aaaa',  -- 55
  $$select public.cambiar_slug_tenant('00000000-0000-0000-0000-0000000065d1', 'vuelta-b')$$),
  'vuelta-b', 'A -> B');
select is(pg_temp.como('platform_admin', '00000000-0000-0000-0000-00000000aaaa',  -- 56
  $$select public.cambiar_slug_tenant('00000000-0000-0000-0000-0000000065d1', 'vuelta-a')$$),
  'vuelta-a', 'B -> A: un tenant puede volver a su slug anterior vigente');
select is((select tenant_id::text || '/' || slug_vigente || '/' || reemplazado::text  -- 57
             from public.resolver_slug_tenant('vuelta-a')),
  '00000000-0000-0000-0000-0000000065d1/vuelta-a/false', 'A resuelve como vigente');
select is((select tenant_id::text || '/' || slug_vigente || '/' || reemplazado::text  -- 58
             from public.resolver_slug_tenant('vuelta-b')),
  '00000000-0000-0000-0000-0000000065d1/vuelta-a/true', 'B redirige a A');
select is((select count(*) from public.slugs_anteriores where slug = 'vuelta-a'), 0::bigint,  -- 59
  'no queda la fila de A en slugs_anteriores');

select * from finish();
rollback;
