-- pseudonymize_channel_user y la clave unica channel_user_hmac_key en Vault (Ola 4, tarea 13).
--
-- Limite de la prueba: Vault (secrets_name_idx, unico por nombre, de su propiedad) impide en esta base
-- crear un segundo secreto con el mismo nombre, y ni el rol de migraciones ni el de pgTAP pueden quitar ese
-- indice. Por eso la rama "mas de una clave -> NULL" de la funcion (defensa en profundidad para versiones de
-- Vault sin ese indice) no se puede ejercitar aca; lo que se prueba es que el segundo secreto ni siquiera
-- puede existir y que la funcion sigue usando la unica clave, con el mismo hash de siempre.
begin;
select plan(6);

delete from vault.secrets where name = 'channel_user_hmac_key';

do $$
begin
  perform vault.create_secret('clave-de-prueba-hmac', 'channel_user_hmac_key');
end
$$;

-- Regresion: con una sola clave el hash es el HMAC-SHA256 de siempre (calculado aparte con la misma clave).
select is(public.pseudonymize_channel_user('00000000-0000-0000-0000-0000000000a1', 'telegram', 'u1'),
  encode(extensions.hmac('00000000-0000-0000-0000-0000000000a1|telegram|u1', 'clave-de-prueba-hmac', 'sha256'), 'hex'),
  'con una sola clave el hash es el HMAC-SHA256 de tenant|canal|usuario');

-- Un segundo secreto con el mismo nombre no puede crearse (queda la unica clave y el mismo hash).
select throws_ok(
  $$select vault.create_secret('clave-nueva-hmac', 'channel_user_hmac_key')$$,
  '23505', null, 'Vault rechaza un segundo secreto con el nombre de la clave');
select is((select count(*) from vault.secrets where name = 'channel_user_hmac_key'), 1::bigint,
  'sigue habiendo una sola clave');
select isnt(public.pseudonymize_channel_user('00000000-0000-0000-0000-0000000000a1', 'telegram', 'u1'),
  encode(extensions.hmac('00000000-0000-0000-0000-0000000000a1|telegram|u1', 'clave-nueva-hmac', 'sha256'), 'hex'),
  'no se calcula ningun hash con la clave nueva');

select is_empty(
  $$select r from unnest(array['bot_service', 'dashboard_service', 'platform_admin', 'platform_admin_readonly',
                               'purge_job', 'scheduler_job', 'anon', 'authenticated', 'service_role']) r
     where has_function_privilege(r, 'public.pseudonymize_channel_user(uuid, text, text)', 'execute')$$,
  'los permisos de ejecucion no cambian: ningun rol de aplicacion');
select matches(obj_description('public.pseudonymize_channel_user(uuid, text, text)'::regprocedure, 'pg_proc'),
  'IRREVERSIBLE PARA EL RECONTACTO', 'el comentario advierte que rotar la clave es irreversible para el recontacto');

select * from finish();
rollback;
