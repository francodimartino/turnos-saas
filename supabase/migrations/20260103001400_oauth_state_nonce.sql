-- Migración de plataforma: nonce de un solo uso para el `state` del OAuth de Google Calendar.
--
-- Problema: el `state` firmado que viaja en el redirect de Google se podia reutilizar (replay) mientras no venciera.
-- Solucion: el `state` lleva un nonce aleatorio; el backend lo "consume" una sola vez al volver de Google. Quien
-- reintente con el mismo `state` obtiene false y se rechaza.
--
-- INTERFAZ PARA EL BACKEND (dashboard-backend)
--   select public.consume_oauth_state_nonce(p_nonce text, p_expira timestamptz) -> boolean
--     - Se llama con el rol dashboard_service, dentro de la transaccion con contexto de tenant
--       (app.current_tenant_id, el mismo que fija platform_tenancy). Sin contexto: insufficient_privilege (42501).
--     - true  = primera vez que este tenant presenta ese nonce y todavia no vencio: el flujo puede seguir.
--     - false = ya consumido por este tenant, o p_expira ya paso: rechazar el callback.
--     - p_nonce: texto no vacio de hasta 200 caracteres (invalid_parameter_value si no).
--     - p_expira: el vencimiento del propio `state` (el `exp` firmado). No puede estar a mas de 1 dia: evita
--       nonces "eternos" (invalid_parameter_value). El backend debe seguir validando firma y `exp` del state;
--       la fila se conserva hasta p_expira y despues se poda, asi que un state vencido nunca llega a este punto.
--     - Atomica: dos consumos concurrentes del mismo nonce => exactamente uno recibe true (clave primaria).
--     - Un mismo valor en dos tenants distintos son dos nonces independientes (la clave incluye tenant_id).
--     - Poda: cada llamada borra los nonces vencidos de todos los tenants (la funcion corre como dueño y no
--       depende de la RLS); no hace falta un job aparte.
--
-- Privilegios (§3.2): la tabla no se otorga a ningun rol de aplicacion (los default privileges ya estan revocados);
-- dashboard_service solo ejecuta la funcion. RLS activa con la policy tenant_isolation por defensa en profundidad.
-- La tabla no guarda datos personales: solo el nonce aleatorio y su vencimiento.

create table public.oauth_state_nonces (
  tenant_id  uuid        not null references public.tenants (id) on delete cascade,
  nonce      text        not null check (length(nonce) between 1 and 200),
  expires_at timestamptz not null,
  created_at timestamptz not null default now(),
  primary key (tenant_id, nonce)
);

create index oauth_state_nonces_expires_at_idx on public.oauth_state_nonces (expires_at);

alter table public.oauth_state_nonces enable row level security;

create policy tenant_isolation on public.oauth_state_nonces
  using (tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid);

revoke all on public.oauth_state_nonces from public, anon, authenticated, service_role;

create function public.consume_oauth_state_nonce(p_nonce text, p_expira timestamptz)
returns boolean
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_ctx uuid := nullif(current_setting('app.current_tenant_id', true), '')::uuid;
  v_filas integer;
begin
  if v_ctx is null then
    raise exception 'contexto de tenant requerido' using errcode = 'insufficient_privilege';
  end if;

  if p_nonce is null or length(p_nonce) not between 1 and 200 then
    raise exception 'nonce invalido' using errcode = 'invalid_parameter_value';
  end if;
  if p_expira is null or p_expira > now() + interval '1 day' then
    raise exception 'vencimiento invalido' using errcode = 'invalid_parameter_value';
  end if;

  -- poda de vencidos (todos los tenants)
  delete from public.oauth_state_nonces n where n.expires_at <= now();

  -- un state ya vencido no se consume
  if p_expira <= now() then
    return false;
  end if;

  insert into public.oauth_state_nonces (tenant_id, nonce, expires_at)
  values (v_ctx, p_nonce, p_expira)
  on conflict (tenant_id, nonce) do nothing;

  get diagnostics v_filas = row_count;
  return v_filas = 1;
end
$$;

revoke all on function public.consume_oauth_state_nonce(text, timestamptz)
  from public, anon, authenticated, service_role;
grant execute on function public.consume_oauth_state_nonce(text, timestamptz) to dashboard_service;
