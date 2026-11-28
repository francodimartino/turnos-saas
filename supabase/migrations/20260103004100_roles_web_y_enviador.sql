-- Roles de login de la web de reservas (web_reservas) y del servicio enviador (enviador), de mínimo privilegio (§3.2).
--
-- Los dos nacen como el resto de los roles de login: LOGIN, sin superuser, sin BYPASSRLS, sin herencia y sin
-- contraseña versionada (se asigna fuera del repo, con scripts/setup_proyecto.py y SETUP_PASSWORD_<ROL>).
-- Migración aditiva: crea dos roles y otorga privilegios; no cambia ni borra nada existente.
--
-- web_reservas (servicio público, sin cuenta del cliente final): solo lo que usa para mostrar el negocio, calcular
--   huecos, reservar, cancelar, mover y encolar avisos. Sin acceso a tenant_integrations, Vault, audit_log,
--   tenant_users ni get_integration_secret. Los privilegios sobre sesiones_web, codigos_web y las columnas de la
--   política de privacidad de end_customers se otorgan en la migración que sigue a la creación de esas tablas.
--     SELECT   services, professionals, professional_services, availability_rules, availability_blocks
--     SELECT, INSERT, UPDATE   bookings
--     tenants  SELECT (id, name, settings)
--     end_customers  SELECT (id, tenant_id, name, phone, email, created_at, deleted_at),
--                    INSERT (tenant_id, name, phone, email), UPDATE (name, phone, email)
--     canales_cliente  SELECT; INSERT (tenant_id, end_customer_id, canal, identificador, verificado_at);
--                      UPDATE (verificado_at)
--     booking_packs  SELECT (id, tenant_id, used_sessions), UPDATE (used_sessions): al cancelar un turno que consumió
--                    una sesión de pack, el trigger de bookings la devuelve con los privilegios de quien cancela
--     EXECUTE  resolver_slug_tenant, anonymize_end_customer, enqueue_notification, encolar_aviso_negocio
--   anonymize_end_customer deriva el actor de session_user y hoy rechaza a web_reservas hasta que audit_actor_type lo
--   reconozca. Sin EXECUTE todavía: write_audit_log y release_expired_pending_overlaps.
--
-- enviador (proceso siempre activo que procesa el outbox): ningún privilegio sobre tablas; solo EXECUTE de funciones
--     claim_notification_outbox, complete_notification_outbox, resolve_outbox_delivery, registrar_entrega_outbox,
--     find_integration, get_integration_secret, record_notification_attempt
--   No encola (eso es de quien origina el aviso), no usa el reclamo acotado a Telegram del job de vencimiento y no
--   poda el outbox.

-- ---------------------------------------------------------------------------
-- Roles
-- ---------------------------------------------------------------------------
do $$
declare
  v_rol text;
begin
  foreach v_rol in array array['web_reservas', 'enviador'] loop
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
-- web_reservas: tablas
-- ---------------------------------------------------------------------------
grant select on
  public.services, public.professionals, public.professional_services,
  public.availability_rules, public.availability_blocks
  to web_reservas;

grant select, insert, update on public.bookings to web_reservas;

-- El negocio solo se ve a sí mismo (la policy tenant_isolation) y sin las columnas de cobro ni de ciclo de vida.
grant select (id, name, settings) on public.tenants to web_reservas;

-- La ficha: sin canal de Telegram/WhatsApp heredado, sin pseudónimo y sin extra_data (notas del negocio).
grant select (id, tenant_id, name, phone, email, created_at, deleted_at) on public.end_customers to web_reservas;
grant insert (tenant_id, name, phone, email) on public.end_customers to web_reservas;
grant update (name, phone, email) on public.end_customers to web_reservas;

grant select on public.canales_cliente to web_reservas;
grant insert (tenant_id, end_customer_id, canal, identificador, verificado_at)
  on public.canales_cliente to web_reservas;
grant update (verificado_at) on public.canales_cliente to web_reservas;

grant select (id, tenant_id, used_sessions) on public.booking_packs to web_reservas;
grant update (used_sessions) on public.booking_packs to web_reservas;

-- ---------------------------------------------------------------------------
-- web_reservas: funciones
-- ---------------------------------------------------------------------------
grant execute on function public.resolver_slug_tenant(text) to web_reservas;
grant execute on function public.anonymize_end_customer(uuid) to web_reservas;
grant execute on function public.enqueue_notification(uuid, text, text, jsonb, integer) to web_reservas;
grant execute on function public.encolar_aviso_negocio(text, jsonb, integer) to web_reservas;

-- ---------------------------------------------------------------------------
-- enviador: solo funciones
-- ---------------------------------------------------------------------------
grant execute on function public.claim_notification_outbox(integer, integer, integer) to enviador;
grant execute on function public.complete_notification_outbox(uuid, text, text, integer) to enviador;
grant execute on function public.resolve_outbox_delivery(uuid) to enviador;
grant execute on function public.registrar_entrega_outbox(uuid, text, text, text) to enviador;
grant execute on function public.find_integration(text, uuid) to enviador;
grant execute on function public.get_integration_secret(uuid) to enviador;
grant execute on function public.record_notification_attempt(uuid, text, text, text, integer, text, uuid)
  to enviador;
