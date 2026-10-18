-- pseudonymize_channel_user falla cerrada con mas de una clave channel_user_hmac_key en Vault (Ola 4, tarea 13).
begin;
select plan(7);

delete from vault.secrets where name = 'channel_user_hmac_key';

do $$
begin
  perform vault.create_secret('clave-de-prueba-hmac', 'channel_user_hmac_key');
end
$$;

-- Regresion: con una sola clave el hash es el de siempre (valor fijo calculado con la clave de prueba).
select is(public.pseudonymize_channel_user('00000000-0000-0000-0000-0000000000a1', 'telegram', 'u1'),
  encode(extensions.hmac('00000000-0000-0000-0000-0000000000a1|telegram|u1', 'clave-de-prueba-hmac', 'sha256'), 'hex'),
  'con una sola clave el hash es el HMAC-SHA256 de tenant|canal|usuario');

create temp table hash_previo as
  select public.pseudonymize_channel_user('00000000-0000-0000-0000-0000000000a1', 'telegram', 'u1') as h;

-- Segundo secreto con el mismo nombre: si Vault tiene un indice unico por nombre se lo quita dentro de la
-- transaccion (el rollback lo restituye) para poder reproducir el caso.
do $$
begin
  begin
    execute 'drop index if exists vault.secrets_name_idx';
  exception when others then
    null;
  end;
  insert into vault.secrets (name, secret) values ('channel_user_hmac_key', 'clave-nueva-hmac');
end
$$;

select is((select count(*) from vault.secrets where name = 'channel_user_hmac_key'), 2::bigint,
  'hay dos secretos con el nombre de la clave');
select is(public.pseudonymize_channel_user('00000000-0000-0000-0000-0000000000a1', 'telegram', 'u1'), null,
  'con dos claves devuelve NULL (falla cerrada)');
select isnt(public.pseudonymize_channel_user('00000000-0000-0000-0000-0000000000a1', 'telegram', 'u1'),
  encode(extensions.hmac('00000000-0000-0000-0000-0000000000a1|telegram|u1', 'clave-nueva-hmac', 'sha256'), 'hex'),
  'no se calcula ningun hash con la clave nueva');

delete from vault.secrets where name = 'channel_user_hmac_key' and secret = 'clave-nueva-hmac';
select is(public.pseudonymize_channel_user('00000000-0000-0000-0000-0000000000a1', 'telegram', 'u1'),
  (select h from hash_previo), 'al quedar una sola clave vuelve el mismo hash');

select is_empty(
  $$select r from unnest(array['bot_service', 'dashboard_service', 'platform_admin', 'platform_admin_readonly',
                               'purge_job', 'scheduler_job', 'anon', 'authenticated', 'service_role']) r
     where has_function_privilege(r, 'public.pseudonymize_channel_user(uuid, text, text)', 'execute')$$,
  'los permisos de ejecucion no cambian: ningun rol de aplicacion');
select matches(obj_description('public.pseudonymize_channel_user(uuid, text, text)'::regprocedure, 'pg_proc'),
  'IRREVERSIBLE PARA EL RECONTACTO', 'el comentario advierte que rotar la clave es irreversible para el recontacto');

select * from finish();
rollback;
