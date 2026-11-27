-- Plataforma: el logo del negocio se guarda con un nombre único por subida.
--
-- settings.logo_ruta pasa de '<id del tenant>/logo.<ext>' a '<id del tenant>/logo-<id>.<ext>', con un id de 32
-- hexadecimales en minúscula (uuid sin guiones) que genera el servicio en cada subida. Así dos operaciones sobre el
-- logo de un mismo negocio nunca se pisan: ninguna borra el objeto que otra acaba de guardar. Sigue valiendo solo el
-- logo del propio tenant (el id del tenant tiene que ser el de la fila) y ninguna ruta que escape de su carpeta.
-- El resto de las reglas de tenants.settings no cambia. Un settings con la ruta anterior (sin id) deja de ser válido
-- al escribirse de nuevo; la función de trigger solo se reemplaza (no SECURITY DEFINER, sin cambio de grants ni firma).

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
  v_texto text;
begin
  -- (clave, mínimo, máximo) de cada setting entero opcional.
  for v_clave, v_min, v_max in
    values ('booking_approval_timeout_hours', 1, 168),
           ('booking_max_active_per_customer', 1, 50),
           ('booking_max_per_hour', 1, 60),
           ('booking_min_notice_minutes', 0, 1440),
           ('cancelacion_plazo_horas', 0, 720)
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

  -- Colores de la marca: texto '#RRGGBB'.
  for v_clave in select unnest(array['color_principal', 'color_secundario'])
  loop
    if new.settings ? v_clave then
      v_valor := new.settings -> v_clave;
      if jsonb_typeof(v_valor) is distinct from 'string'
         or (v_valor #>> '{}') !~ '^#[0-9A-Fa-f]{6}$' then
        raise exception 'settings.% debe ser un color #RRGGBB', v_clave
          using errcode = 'check_violation';
      end if;
    end if;
  end loop;

  -- Logo: solo un objeto del propio tenant en el bucket, con un nombre único por subida
  -- (<id del tenant>/logo-<32 hexadecimales>.png|jpg|webp), sin rutas que escapen de su carpeta.
  if new.settings ? 'logo_ruta' then
    v_valor := new.settings -> 'logo_ruta';
    v_texto := case when jsonb_typeof(v_valor) = 'string' then v_valor #>> '{}' end;
    if v_texto is null
       or v_texto !~ ('^' || new.id::text || '/logo-[0-9a-f]{32}[.](png|jpg|webp)$') then
      raise exception 'settings.logo_ruta debe ser <tenant>/logo-<id>.png, .jpg o .webp del propio tenant'
        using errcode = 'check_violation';
    end if;
  end if;
  return new;
end
$$;
