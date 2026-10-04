-- Migración de plataforma 3: RLS en todas las tablas de plataforma, policies y grants por rol.
-- Ver ARCHITECTURE.md §3.1 (RLS), §3.2 (grants por rol), §3.3 (tests) y §4.2.
--
-- Alcance: solo tablas. Las funciones SECURITY DEFINER (y sus EXECUTE) van en migraciones
-- posteriores. Esta migración deja el schema public en "denegado por defecto":
--   1. revoca los grants heredados de Supabase (anon/authenticated/service_role) y de PUBLIC,
--      incluidos los default privileges para tablas futuras;
--   2. activa RLS en el 100% de las tablas de plataforma (sin FORCE: el dueño de las funciones
--      SECURITY DEFINER sigue operando);
--   3. crea las policies de §3.1.3 y §3.1.6;
--   4. otorga a cada rol de login exactamente lo de §3.2 (a nivel de tabla o de columna).
-- Toda tabla nueva (por ejemplo las de dominio) debe activar RLS y declarar sus propios grants:
-- el meta-test de RLS falla si alguna tabla de public queda sin RLS.
--
-- Decisiones que acá se fijan donde §3.2 dice "Write" o no lo detalla:
--   * platform_admin no borra tenants (el ciclo de vida lo cubren status y la purga), ni
--     platform_settings (sin la fila semilla, "sin configurar" podría leerse como 0 días), ni
--     tenant_users (los borra la purga, vía función definer).
--   * platform_admin_readonly lee lo mismo que lee dashboard_service (se reusan sus consultas
--     para impersonar, §2.11.4), y tenant_integrations sin secret_id/config; no lee audit_log
--     (la impersonación no debe mostrar acciones de platform_admin ni su propio rastro).
--   * service_role pierde sus grants por defecto sobre las tablas de public. Su bypass de RLS
--     no sirve sin privilegios de tabla; los scripts de setup (por ejemplo sembrar el primer
--     platform_admin) corren con el rol dueño de las migraciones.

-- ---------------------------------------------------------------------------
-- 1. Grants heredados: fuera
-- ---------------------------------------------------------------------------
revoke all on all tables in schema public from public, anon, authenticated, service_role;
revoke all on all sequences in schema public from public, anon, authenticated, service_role;

-- Línea base idempotente para los 6 roles de login: se parte de cero y se otorga explícito.
revoke all on all tables in schema public
  from bot_service, dashboard_service, platform_admin, platform_admin_readonly, purge_job, scheduler_job;

-- Default privileges: las tablas y secuencias futuras no se otorgan solas a nadie.
-- Supabase los define para el rol que crea los objetos (postgres y supabase_admin): se revocan
-- para cada rol que tenga default privileges en public y a nivel global.
do $$
declare
  v_dueno record;
begin
  for v_dueno in
    select distinct d.defaclrole::regrole::text as rol
    from pg_default_acl d
    where d.defaclnamespace in (0, 'public'::regnamespace)
      and d.defaclobjtype in ('r', 'S')
    union
    select current_user::text
  loop
    begin
      execute format(
        'alter default privileges for role %s in schema public revoke all on tables from public, anon, authenticated, service_role',
        v_dueno.rol);
      execute format(
        'alter default privileges for role %s in schema public revoke all on sequences from public, anon, authenticated, service_role',
        v_dueno.rol);
    exception when insufficient_privilege then
      -- sin membresía en ese rol no se pueden tocar sus default privileges: no se oculta
      raise warning 'No se pudieron revocar los default privileges de %: revocarlos con ese rol', v_dueno.rol;
    end;
  end loop;
end
$$;

-- Los roles de aplicación usan el schema public (sin poder crear objetos en él)
grant usage on schema public
  to bot_service, dashboard_service, platform_admin, platform_admin_readonly, purge_job, scheduler_job;

-- ---------------------------------------------------------------------------
-- 2. RLS activada en el 100% de las tablas de plataforma (§3.1.1)
-- ---------------------------------------------------------------------------
alter table public.tenants                 enable row level security;
alter table public.tenant_users            enable row level security;
alter table public.permissions             enable row level security;
alter table public.tenant_user_permissions enable row level security;
alter table public.platform_settings       enable row level security;
alter table public.platform_admins         enable row level security;
alter table public.tenant_integrations     enable row level security;
alter table public.end_customers           enable row level security;
alter table public.audit_log               enable row level security;
alter table public.inbound_messages        enable row level security;
alter table public.bot_sessions            enable row level security;
alter table public.notifications_log       enable row level security;
alter table public.payments                enable row level security;

-- ---------------------------------------------------------------------------
-- 3. Policies de aislamiento por tenant (§3.1.3)
-- El contexto lo setea platform_tenancy en cada transacción (set local app.current_tenant_id).
-- nullif: en una conexión reutilizada el GUC vale '' (no NULL) y ''::uuid lanzaría error.
-- platform_admin_readonly bypassea el chequeo de tenant por current_user (impersonación, §3.2);
-- solo tiene SELECT, así que el bypass nunca alcanza una escritura.
-- ---------------------------------------------------------------------------
create policy tenant_isolation on public.tenant_users
  using (
    tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid
    or current_user = 'platform_admin_readonly'
  );

create policy tenant_isolation on public.tenant_user_permissions
  using (
    tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid
    or current_user = 'platform_admin_readonly'
  );

create policy tenant_isolation on public.tenant_integrations
  using (
    tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid
    or current_user = 'platform_admin_readonly'
  );

create policy tenant_isolation on public.end_customers
  using (
    tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid
    or current_user = 'platform_admin_readonly'
  );

create policy tenant_isolation on public.audit_log
  using (
    tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid
    or current_user = 'platform_admin_readonly'
  );

create policy tenant_isolation on public.inbound_messages
  using (
    tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid
    or current_user = 'platform_admin_readonly'
  );

create policy tenant_isolation on public.bot_sessions
  using (
    tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid
    or current_user = 'platform_admin_readonly'
  );

create policy tenant_isolation on public.notifications_log
  using (
    tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid
    or current_user = 'platform_admin_readonly'
  );

create policy tenant_isolation on public.payments
  using (
    tenant_id = nullif(current_setting('app.current_tenant_id', true), '')::uuid
    or current_user = 'platform_admin_readonly'
  );

-- ---------------------------------------------------------------------------
-- 4. Policies de las tablas sin tenant_id directo o con acceso de plataforma (§3.1.6)
-- ---------------------------------------------------------------------------

-- tenants: bot_service/dashboard_service solo su fila; platform_admin_readonly, todas;
-- platform_admin, todas (policy explícita, sin BYPASSRLS).
create policy tenant_isolation on public.tenants
  using (
    id = nullif(current_setting('app.current_tenant_id', true), '')::uuid
    or current_user = 'platform_admin_readonly'
  );

create policy platform_admin_acceso_total on public.tenants
  to platform_admin
  using (true) with check (true);

-- tenant_users y tenant_integrations: además de la policy por tenant, acceso explícito de
-- platform_admin (create_tenant y la carga de credenciales operan sin contexto de tenant).
create policy platform_admin_acceso_total on public.tenant_users
  to platform_admin
  using (true) with check (true);

create policy platform_admin_acceso_total on public.tenant_integrations
  to platform_admin
  using (true) with check (true);

-- permissions: catálogo sin datos de tenant, lectura para los roles de aplicación.
create policy permissions_lectura on public.permissions
  for select
  to bot_service, dashboard_service, platform_admin, platform_admin_readonly
  using (true);

-- platform_settings, platform_admins: solo platform_admin. Los demás roles no tienen policy
-- (denegado por defecto).
create policy platform_admin_acceso_total on public.platform_settings
  to platform_admin
  using (true) with check (true);

create policy platform_admin_acceso_total on public.platform_admins
  to platform_admin
  using (true) with check (true);

-- audit_log: platform_admin lee todo; dashboard_service, su tenant y además nunca las acciones
-- de platform_admin (la impersonación no se expone al tenant, §2.11.4). La restricción es
-- RESTRICTIVE: se combina con AND sobre tenant_isolation.
create policy platform_admin_lectura on public.audit_log
  for select
  to platform_admin
  using (true);

create policy sin_acciones_de_platform_admin on public.audit_log
  as restrictive
  for select
  to dashboard_service
  using (actor_type <> 'platform_admin');

-- notifications_log: SELECT de platform_admin en todas las filas (visor de §2.11.7).
create policy platform_admin_lectura on public.notifications_log
  for select
  to platform_admin
  using (true);

-- ---------------------------------------------------------------------------
-- 5. Grants por rol (§3.2)
-- ---------------------------------------------------------------------------

-- bot_service: turnos aparte (migración de dominio). Sin SELECT en tenant_integrations, sin
-- DELETE salvo bot_sessions, sin escritura directa a tenant_users/audit_log.
grant select on public.tenants to bot_service;
grant select, insert, update on public.end_customers to bot_service;
grant select, insert, update on public.notifications_log to bot_service;
grant select, insert, update on public.payments to bot_service;
grant insert on public.inbound_messages to bot_service;
grant select, insert, update, delete on public.bot_sessions to bot_service;

-- dashboard_service: scopeado por RLS, grants por tabla y por columna.
grant select on public.tenants to dashboard_service;
grant update (settings) on public.tenants to dashboard_service;
grant select, insert, update on public.tenant_users to dashboard_service;
grant select, insert, delete on public.tenant_user_permissions to dashboard_service;
grant select on public.permissions to dashboard_service;
grant select, insert, update on public.end_customers to dashboard_service;
grant select (id, tenant_id, integration_type, professional_id, created_at)
  on public.tenant_integrations to dashboard_service;
grant select on public.audit_log to dashboard_service;
grant select, insert, update on public.notifications_log to dashboard_service;

-- platform_admin: escritura en tenants/platform_settings/platform_admins/tenant_users;
-- lectura cross-tenant de audit_log y notifications_log y de tenant_integrations (el alta de
-- credenciales va por la función store_tenant_integration). Sin acceso a datos operativos.
grant select, insert, update on public.tenants to platform_admin;
grant select, insert, update on public.platform_settings to platform_admin;
grant select, insert, delete on public.platform_admins to platform_admin;
grant select, insert, update on public.tenant_users to platform_admin;
grant select on public.tenant_integrations to platform_admin;
grant select on public.audit_log to platform_admin;
grant select on public.notifications_log to platform_admin;

-- platform_admin_readonly: solo SELECT, sobre lo que lee el dashboard del tenant.
grant select on public.tenants to platform_admin_readonly;
grant select on public.tenant_users to platform_admin_readonly;
grant select on public.permissions to platform_admin_readonly;
grant select on public.tenant_user_permissions to platform_admin_readonly;
grant select on public.end_customers to platform_admin_readonly;
grant select (id, tenant_id, integration_type, professional_id, created_at)
  on public.tenant_integrations to platform_admin_readonly;
grant select on public.notifications_log to platform_admin_readonly;

-- purge_job y scheduler_job: sin grants sobre tablas. Solo EXECUTE de funciones SECURITY
-- DEFINER, que se otorgan en las migraciones de funciones.
