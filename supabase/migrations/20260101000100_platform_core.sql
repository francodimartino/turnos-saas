-- Migración de plataforma 1: roles de login, tablas núcleo de tenancy e invariantes.
-- Ver ARCHITECTURE.md §2.2, §2.4, §2.9, §2.10, §3.2 y §4.2.
--
-- Alcance: roles de login, extensiones, tablas, checks y triggers de invariantes.
-- RLS, policies y grants van en la migración siguiente; las funciones de negocio, después.
--
-- CONTRASEÑAS DE LOS ROLES DE LOGIN: no se versionan. Los roles se crean con LOGIN pero
-- sin contraseña (no pueden autenticarse hasta que se les asigne una). Después de aplicar
-- las migraciones, cada contraseña se fija fuera del repo, una vez por proyecto, con un
-- usuario administrador de la base y el valor tomado del gestor de secretos del entorno:
--   alter role bot_service with password '<valor del secreto BOT_SERVICE_DB_PASSWORD>';
-- (idem dashboard_service, platform_admin, platform_admin_readonly, purge_job, scheduler_job).
-- Se ejecuta desde una terminal, nunca pegando el valor en un archivo versionado.

-- ---------------------------------------------------------------------------
-- Extensiones
-- ---------------------------------------------------------------------------
create extension if not exists btree_gist with schema extensions;
create extension if not exists pgcrypto with schema extensions;

-- ---------------------------------------------------------------------------
-- Roles de login (§3.2): uno por backend, sin BYPASSRLS, sin contraseña versionada
-- ---------------------------------------------------------------------------
do $$
declare
  v_rol text;
begin
  foreach v_rol in array array[
    'bot_service', 'dashboard_service', 'platform_admin',
    'platform_admin_readonly', 'purge_job', 'scheduler_job'
  ] loop
    if not exists (select 1 from pg_roles where rolname = v_rol) then
      execute format(
        'create role %I login nosuperuser nocreatedb nocreaterole noinherit noreplication nobypassrls',
        v_rol
      );
    end if;
  end loop;
end
$$;

-- ---------------------------------------------------------------------------
-- Tablas (§2.10)
-- ---------------------------------------------------------------------------
create table public.tenants (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  slug text not null unique,
  plan text not null default 'pilot' check (plan in ('pilot')),
  status text not null default 'active'
    check (status in ('active', 'suspended', 'inactive', 'purged')),
  deactivated_at timestamptz null,
  settings jsonb not null default '{}' check (jsonb_typeof(settings) = 'object'),
  paid_until date null,
  billing_notes text null,
  created_at timestamptz not null default now(),
  -- un inactive sin fecha nunca se purgaría
  check (status <> 'inactive' or deactivated_at is not null)
);

create table public.tenant_users (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id),
  -- nullable: la purga elimina la cuenta de Auth y la fila sobrevive para la trazabilidad
  auth_user_id uuid null unique references auth.users (id) on delete set null,
  is_owner boolean not null default false,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  -- clave objetivo de las FKs compuestas de las tablas hijas (nota de RLS de §7)
  unique (tenant_id, id)
);

create index tenant_users_tenant_id_idx on public.tenant_users (tenant_id);

create table public.permissions (
  key text primary key
);

create table public.tenant_user_permissions (
  -- desnormalizado desde tenant_users.tenant_id y forzado por trigger
  tenant_id uuid not null,
  tenant_user_id uuid not null,
  permission_key text not null references public.permissions (key),
  granted_at timestamptz not null default now(),
  primary key (tenant_user_id, permission_key),
  foreign key (tenant_id, tenant_user_id)
    references public.tenant_users (tenant_id, id)
);

create index tenant_user_permissions_tenant_id_idx on public.tenant_user_permissions (tenant_id);

create table public.platform_settings (
  key text primary key,
  value jsonb not null
);

create table public.platform_admins (
  auth_user_id uuid primary key references auth.users (id) on delete cascade,
  added_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Datos semilla
-- ---------------------------------------------------------------------------
-- Si la tabla quedara vacía, "sin configurar" podría leerse como 0 días y purgar todo lo
-- inactivo de inmediato: el valor inicial es una fila real.
insert into public.platform_settings (key, value)
values ('tenant_deletion_grace_period_days', '90'::jsonb)
on conflict (key) do nothing;

insert into public.permissions (key)
values ('view_bookings'), ('approve_bookings'), ('edit_prices'), ('manage_staff'), ('view_reports')
on conflict (key) do nothing;

-- ---------------------------------------------------------------------------
-- Trigger: transiciones válidas de tenants.status (§2.2)
-- active <-> suspended, active/suspended -> inactive, inactive -> active, inactive -> purged.
-- purged es terminal.
-- ---------------------------------------------------------------------------
create function public.tenants_validar_transicion_status()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.status is not distinct from old.status then
    return new;
  end if;

  if not (
    (old.status = 'active'    and new.status in ('suspended', 'inactive'))
    or (old.status = 'suspended' and new.status in ('active', 'inactive'))
    or (old.status = 'inactive'  and new.status in ('active', 'purged'))
  ) then
    raise exception 'Transición de estado de tenant inválida: % -> %', old.status, new.status
      using errcode = 'check_violation';
  end if;

  if new.status = 'inactive' then
    -- arranca el reloj de purga
    new.deactivated_at := coalesce(new.deactivated_at, now());
  elsif new.status in ('active', 'suspended') then
    -- reactivar (o suspender) nunca deja un reloj de purga corriendo
    new.deactivated_at := null;
  end if;

  return new;
end
$$;

create trigger tenants_validar_transicion_status
  before update of status on public.tenants
  for each row execute function public.tenants_validar_transicion_status();

-- ---------------------------------------------------------------------------
-- Trigger: validación de tenants.settings (§2.10, §7.1)
-- 'booking_approval_timeout_hours', si está, es un entero entre 1 y 168.
-- ---------------------------------------------------------------------------
create function public.tenants_validar_settings()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_valor jsonb;
  v_horas numeric;
begin
  if new.settings ? 'booking_approval_timeout_hours' then
    v_valor := new.settings -> 'booking_approval_timeout_hours';
    if jsonb_typeof(v_valor) is distinct from 'number' then
      raise exception 'settings.booking_approval_timeout_hours debe ser un entero entre 1 y 168'
        using errcode = 'check_violation';
    end if;
    v_horas := (v_valor #>> '{}')::numeric;
    if v_horas <> trunc(v_horas) or v_horas < 1 or v_horas > 168 then
      raise exception 'settings.booking_approval_timeout_hours debe ser un entero entre 1 y 168'
        using errcode = 'check_violation';
    end if;
  end if;
  return new;
end
$$;

create trigger tenants_validar_settings
  before insert or update of settings on public.tenants
  for each row execute function public.tenants_validar_settings();

-- ---------------------------------------------------------------------------
-- Trigger: invariante del último owner activo (§2.9)
-- No se puede borrar, desactivar, quitarle is_owner ni mover de tenant al último owner activo.
-- ---------------------------------------------------------------------------
create function public.tenant_users_proteger_ultimo_owner()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if not (old.is_owner and old.is_active) then
    -- no era un owner activo: nada que proteger
    if tg_op = 'DELETE' then
      return old;
    end if;
    return new;
  end if;

  if tg_op = 'UPDATE'
     and new.is_owner and new.is_active and new.tenant_id = old.tenant_id then
    return new;
  end if;

  -- serializa cambios concurrentes sobre los owners del mismo tenant
  perform pg_advisory_xact_lock(hashtextextended('tenant_owners:' || old.tenant_id::text, 0));

  if not exists (
    select 1
    from public.tenant_users tu
    where tu.tenant_id = old.tenant_id
      and tu.id <> old.id
      and tu.is_owner
      and tu.is_active
  ) then
    raise exception 'No se puede eliminar, desactivar ni quitar is_owner al último owner activo del tenant %', old.tenant_id
      using errcode = 'check_violation';
  end if;

  if tg_op = 'DELETE' then
    return old;
  end if;
  return new;
end
$$;

create trigger tenant_users_proteger_ultimo_owner_upd
  before update of is_owner, is_active, tenant_id on public.tenant_users
  for each row execute function public.tenant_users_proteger_ultimo_owner();

create trigger tenant_users_proteger_ultimo_owner_del
  before delete on public.tenant_users
  for each row execute function public.tenant_users_proteger_ultimo_owner();

-- ---------------------------------------------------------------------------
-- Trigger: tenant_user_permissions.tenant_id se fuerza desde tenant_users (§2.4)
-- Corre como invocador: si el tenant_user no es visible para quien escribe, falla.
-- ---------------------------------------------------------------------------
create function public.tenant_user_permissions_forzar_tenant_id()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_tenant_id uuid;
begin
  select tu.tenant_id into v_tenant_id
  from public.tenant_users tu
  where tu.id = new.tenant_user_id;

  if v_tenant_id is null then
    raise exception 'tenant_user % inexistente', new.tenant_user_id
      using errcode = 'foreign_key_violation';
  end if;

  new.tenant_id := v_tenant_id;
  return new;
end
$$;

create trigger tenant_user_permissions_forzar_tenant_id
  before insert or update on public.tenant_user_permissions
  for each row execute function public.tenant_user_permissions_forzar_tenant_id();

-- ---------------------------------------------------------------------------
-- Trigger: no se puede borrar la última fila de platform_admins (§2.10)
-- También frena el cascade desde auth.users.
-- ---------------------------------------------------------------------------
create function public.platform_admins_proteger_ultimo()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  perform pg_advisory_xact_lock(hashtextextended('platform_admins', 0));

  if not exists (
    select 1 from public.platform_admins pa where pa.auth_user_id <> old.auth_user_id
  ) then
    raise exception 'No se puede eliminar el último platform_admin'
      using errcode = 'check_violation';
  end if;

  return old;
end
$$;

create trigger platform_admins_proteger_ultimo
  before delete on public.platform_admins
  for each row execute function public.platform_admins_proteger_ultimo();
