-- pseudonymize_channel_user: falla cerrada si en Vault hay mas de una clave 'channel_user_hmac_key'.
--
-- Antes tomaba la mas reciente (order by created_at desc limit 1): un segundo secreto con el mismo nombre
-- pasaba a usarse en silencio y todo hash nuevo dejaba de coincidir con el de las filas ya anonimizadas
-- (se perdia el recontacto sin ningun aviso). Ahora, con mas de una clave devuelve NULL, igual que sin
-- clave: la anonimizacion y la reserva fallan cerradas (no se calcula ningun hash con una clave dudosa) y
-- el dato queda a la vista de quien opera. No levanta excepcion para no filtrar nada en mensajes.
--
-- Con una sola clave el resultado es identico al de antes (mismo HMAC-SHA256 sobre tenant|canal|usuario).
-- Misma firma, SECURITY DEFINER y search_path vacio; los permisos no cambian (EXECUTE revocado de PUBLIC,
-- anon, authenticated y service_role; se repite el revoke por claridad). No es destructiva: no toca datos.

create or replace function public.pseudonymize_channel_user(p_tenant_id uuid, p_channel text, p_channel_user_id text)
returns text
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_key text;
  v_cantidad integer;
begin
  if p_tenant_id is null or p_channel is null or p_channel_user_id is null then
    return null;
  end if;

  select count(*), max(ds.decrypted_secret)
    into v_cantidad, v_key
    from vault.decrypted_secrets ds
   where ds.name = 'channel_user_hmac_key';

  -- Ninguna clave, o mas de una: no se calcula nada.
  if v_cantidad <> 1 or v_key is null or v_key = '' then
    return null;
  end if;

  return encode(
    extensions.hmac(p_tenant_id::text || '|' || p_channel || '|' || p_channel_user_id, v_key, 'sha256'),
    'hex');
end
$$;

revoke all on function public.pseudonymize_channel_user(uuid, text, text)
  from public, anon, authenticated, service_role;

comment on function public.pseudonymize_channel_user(uuid, text, text) is
  'HMAC-SHA256 hex de tenant|canal|usuario con la clave channel_user_hmac_key de Vault. Devuelve NULL (falla cerrada) sin clave o con mas de una clave de ese nombre. ROTAR LA CLAVE ES IRREVERSIBLE PARA EL RECONTACTO: los hashes de las filas ya anonimizadas se calcularon con la clave anterior y no se pueden recalcular (el identificador original ya se borro), asi que con una clave nueva esas personas dejan de reconocerse al volver a escribir.';
