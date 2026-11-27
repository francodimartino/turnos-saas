-- Migración de plataforma: resolución pública del slug de un tenant, historial de slugs y slugs reservados (§2.12).
--
-- La web de reservas vive en agendemos.ar/<slug>. Antes de tener contexto de tenant, el backend de la web resuelve el
-- slug con una función SECURITY DEFINER (mismo patrón que resolve_inbound_tenant, §2.6) que solo devuelve tenants
-- 'active': un tenant suspendido, inactivo, purgado o inexistente no se distingue de un slug que no existe.
--
-- INTERFAZ
--   public.resolver_slug_tenant(p_slug text)
--     returns table (tenant_id uuid, nombre text, slug_vigente text, reemplazado boolean)
--     - Devuelve una fila si p_slug es el slug de un tenant activo (reemplazado = false) o un slug anterior que
--       todavía está dentro de su ventana de redirección (reemplazado = true; slug_vigente es el actual, al que el
--       backend redirige). Nada más: ningún otro dato del tenant.
--     - EXECUTE: solo el rol de conexión de la web de reservas, que se otorga junto con ese rol. Mientras no
--       exista, nadie la ejecuta.
--   public.cambiar_slug_tenant(p_tenant_id uuid, p_slug_nuevo text) returns text
--     - EXECUTE: solo platform_admin. Exige actor verificado (app.current_actor_id). Valida formato (minúsculas,
--       números y guiones; 3 a 40 caracteres), reservados y unicidad contra tenants y contra slugs anteriores
--       vigentes. Guarda el slug viejo en slugs_anteriores por 180 días (la ventana de redirección) y deja
--       audit_log con el actor. Devuelve el slug nuevo.
--   public.slug_reservado(p_slug text) returns boolean: la lista de nombres que no puede tomar un negocio.
--
-- La lista de reservados vive en platform_admin.slugs_reservados (código) y acá; un test las compara.

-- ---------------------------------------------------------------------------
-- 1. Slugs reservados
-- ---------------------------------------------------------------------------
create function public.slug_reservado(p_slug text)
returns boolean
language sql
immutable
set search_path = ''
as $$
  select p_slug = any (array[
    'admin', 'admin-api', 'api', 'app', 'assets', 'ayuda', 'blog', 'bot', 'contacto', 'cuenta', 'dashboard',
    'docs', 'health', 'login', 'logout', 'panel', 'precios', 'privacidad', 'registro', 'reservas',
    'reservas-api', 'soporte', 'static', 'status', 'terminos', 'webhook', 'webhooks', 'www'
  ]::text[])
$$;

revoke all on function public.slug_reservado(text) from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- 2. Historial de slugs: el link viejo sigue resolviendo hasta vigente_hasta
-- ---------------------------------------------------------------------------
create table public.slugs_anteriores (
  slug text primary key,
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  reemplazado_at timestamptz not null default now(),
  vigente_hasta timestamptz not null,
  check (vigente_hasta > reemplazado_at)
);

create index slugs_anteriores_tenant_idx on public.slugs_anteriores (tenant_id);

alter table public.slugs_anteriores enable row level security;

create policy tenant_isolation on public.slugs_anteriores
  using (tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid);

revoke all on public.slugs_anteriores from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- 3. Trigger sobre tenants: alta o cambio de slug (también lo ejecuta create_tenant)
--    Rechaza un slug reservado y uno que sea el anterior, aún vigente, de otro tenant.
-- ---------------------------------------------------------------------------
create function public.tenants_validar_slug()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if tg_op = 'UPDATE' and new.slug is not distinct from old.slug then
    return new;
  end if;

  if public.slug_reservado(new.slug) then
    raise exception 'slug reservado: %', new.slug using errcode = 'check_violation';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('slug:' || new.slug, 0));

  if exists (
    select 1 from public.slugs_anteriores s
     where s.slug = new.slug and s.vigente_hasta > now() and s.tenant_id is distinct from new.id
  ) then
    raise exception 'slug en uso como enlace anterior de otro negocio: %', new.slug
      using errcode = 'unique_violation';
  end if;

  return new;
end
$$;

revoke all on function public.tenants_validar_slug() from public, anon, authenticated, service_role;

create trigger tenants_validar_slug
  before insert or update of slug on public.tenants
  for each row execute function public.tenants_validar_slug();

-- ---------------------------------------------------------------------------
-- 4. Resolución pública (solo tenants activos)
-- ---------------------------------------------------------------------------
create function public.resolver_slug_tenant(p_slug text)
returns table (tenant_id uuid, nombre text, slug_vigente text, reemplazado boolean)
language sql
stable
security definer
set search_path = ''
as $$
  select t.id, t.name, t.slug, false
    from public.tenants t
   where p_slug is not null and t.slug = p_slug and t.status = 'active'
  union all
  select t.id, t.name, t.slug, true
    from public.slugs_anteriores s
    join public.tenants t on t.id = s.tenant_id
   where p_slug is not null and s.slug = p_slug and s.vigente_hasta > now() and t.status = 'active'
$$;

revoke all on function public.resolver_slug_tenant(text) from public, anon, authenticated, service_role;

-- ---------------------------------------------------------------------------
-- 5. Cambio de slug (solo platform_admin)
-- ---------------------------------------------------------------------------
create function public.cambiar_slug_tenant(p_tenant_id uuid, p_slug_nuevo text)
returns text
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_actor_id uuid := nullif(current_setting('app.current_actor_id', true), '')::uuid;
  v_status text;
  v_slug_actual text;
begin
  if v_actor_id is null then
    raise exception 'actor verificado requerido' using errcode = 'insufficient_privilege';
  end if;
  if p_slug_nuevo is null or p_slug_nuevo !~ '^[a-z0-9]+(-[a-z0-9]+)*$' or length(p_slug_nuevo) not between 3 and 40 then
    raise exception 'slug inválido: minúsculas, números y guiones, de 3 a 40 caracteres'
      using errcode = 'invalid_parameter_value';
  end if;
  if public.slug_reservado(p_slug_nuevo) then
    raise exception 'slug reservado: %', p_slug_nuevo using errcode = 'check_violation';
  end if;

  select t.status, t.slug into v_status, v_slug_actual from public.tenants t where t.id = p_tenant_id for update;
  if not found then
    raise exception 'tenant inexistente' using errcode = 'no_data_found';
  end if;
  if v_status in ('purging', 'purged') then
    raise exception 'el tenant está en purga o purgado: no se le cambia el slug' using errcode = 'check_violation';
  end if;
  if p_slug_nuevo = v_slug_actual then
    raise exception 'el slug ya es el vigente' using errcode = 'invalid_parameter_value';
  end if;

  perform pg_advisory_xact_lock(hashtextextended('slug:' || p_slug_nuevo, 0));
  perform pg_advisory_xact_lock(hashtextextended('slug:' || v_slug_actual, 0));

  if exists (select 1 from public.tenants t where t.slug = p_slug_nuevo) then
    raise exception 'ya existe un tenant con ese slug' using errcode = 'unique_violation';
  end if;
  if exists (
    select 1 from public.slugs_anteriores s
     where s.slug = p_slug_nuevo and s.vigente_hasta > now() and s.tenant_id <> p_tenant_id
  ) then
    raise exception 'el slug es el enlace anterior, aún vigente, de otro negocio' using errcode = 'unique_violation';
  end if;

  -- el slug nuevo deja de ser "anterior" (volver a uno propio, o reusar uno cuya ventana ya venció)
  delete from public.slugs_anteriores s where s.slug = p_slug_nuevo;
  -- el slug que se deja queda 180 días redirigiendo al nuevo
  delete from public.slugs_anteriores s where s.slug = v_slug_actual;
  insert into public.slugs_anteriores (slug, tenant_id, reemplazado_at, vigente_hasta)
  values (v_slug_actual, p_tenant_id, now(), now() + interval '180 days');

  update public.tenants t set slug = p_slug_nuevo where t.id = p_tenant_id;

  perform public.audit_log_append(p_tenant_id, 'platform_admin', v_actor_id, 'tenant_slug_change', 'tenants',
    p_tenant_id, jsonb_build_object('slug_anterior', v_slug_actual, 'slug_nuevo', p_slug_nuevo));

  return p_slug_nuevo;
end
$$;

revoke all on function public.cambiar_slug_tenant(uuid, text) from public, anon, authenticated, service_role;
grant execute on function public.cambiar_slug_tenant(uuid, text) to platform_admin;
