-- Plataforma: validación en la base de los límites de reserva del bot por tenant (tenants.settings).
--
-- El trigger tenants_validar_settings solo validaba booking_approval_timeout_hours. Se extiende a tres claves
-- opcionales nuevas con los límites anti-abuso de las reservas del bot. Todas son opcionales: un settings que no las
-- trae es válido (aplica el default). Si están, deben ser un número JSON entero dentro del rango; un string, un
-- booleano, un null JSON, un decimal o un valor fuera de rango se rechaza con check_violation (23514).
--
-- INTERFAZ PARA EL BOT (el bot lo lee): claves, rangos y defaults
--   settings.booking_max_active_per_customer  entero 1..50    default 5   turnos futuros activos por cliente final
--   settings.booking_max_per_hour             entero 1..60    default 3   reservas por hora de un mismo cliente final
--   settings.booking_min_notice_minutes       entero 0..1440  default 30  anticipación mínima de un turno (0 = sin mínimo)
--   (booking_approval_timeout_hours sigue igual: entero 1..168, default 24.)
--   El default NO se guarda en la base: lo aplica el bot cuando la clave falta. Cada tenant puede sobrescribirlo.
--   Cotas: máximos altos pero finitos para que un valor absurdo no desactive el límite en la práctica
--   (50 turnos activos, 60 reservas/hora = una por minuto, 1440 minutos = 24 h de anticipación).
--
-- Solo se reemplaza el cuerpo de una función de trigger (no SECURITY DEFINER, sin cambio de grants ni de firma);
-- los settings existentes siguen siendo válidos porque las claves nuevas no existen todavía en ellos.

create or replace function public.tenants_validar_settings()
returns trigger
language plpgsql
set search_path = ''
as $$
declare
  v_valor jsonb;
  v_num numeric;
  v_clave text;
  v_min int;
  v_max int;
begin
  -- (clave, mínimo, máximo) de cada setting entero opcional.
  for v_clave, v_min, v_max in
    values ('booking_approval_timeout_hours', 1, 168),
           ('booking_max_active_per_customer', 1, 50),
           ('booking_max_per_hour', 1, 60),
           ('booking_min_notice_minutes', 0, 1440)
  loop
    if new.settings ? v_clave then
      v_valor := new.settings -> v_clave;
      if jsonb_typeof(v_valor) is distinct from 'number' then
        raise exception 'settings.% debe ser un entero entre % y %', v_clave, v_min, v_max
          using errcode = 'check_violation';
      end if;
      v_num := (v_valor #>> '{}')::numeric;
      if v_num <> trunc(v_num) or v_num < v_min or v_num > v_max then
        raise exception 'settings.% debe ser un entero entre % y %', v_clave, v_min, v_max
          using errcode = 'check_violation';
      end if;
    end if;
  end loop;
  return new;
end
$$;
