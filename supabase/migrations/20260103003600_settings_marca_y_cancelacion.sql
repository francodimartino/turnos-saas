-- Plataforma: validación en la base de las claves de tenants.settings de la web de reservas (plazo de cancelación,
-- colores de la marca y logo).
--
-- El trigger tenants_validar_settings sigue validando las claves enteras de reserva y suma:
--   settings.cancelacion_plazo_horas  entero 0..720  default 24  horas antes del turno hasta las que el cliente puede
--                                                                 cancelar o mover (0 = hasta el último momento)
--   settings.color_principal          texto '#RRGGBB' (6 dígitos hexadecimales, con '#', sin alfa ni abreviado)
--   settings.color_secundario         texto '#RRGGBB'
--   settings.logo_ruta                texto '<id del tenant>/logo.png|jpg|webp': solo el logo del propio tenant
-- Todas son opcionales: un settings que no las trae es válido. Si están y no cumplen la regla (otro tipo, un null JSON,
-- un decimal, fuera de rango, otro formato de color, una ruta de otro tenant o que escape de su carpeta) se rechaza con
-- check_violation (23514). El default de la cancelación NO se guarda en la base: lo aplica el dominio cuando la clave
-- falta. Un settings anterior sigue siendo válido porque las claves nuevas no existen en él.
--
-- Solo se reemplaza el cuerpo de una función de trigger (no SECURITY DEFINER, sin cambio de grants ni de firma).

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

  -- Logo: solo la ruta exacta del propio tenant en el bucket (sin rutas que escapen de su carpeta).
  if new.settings ? 'logo_ruta' then
    v_valor := new.settings -> 'logo_ruta';
    v_texto := case when jsonb_typeof(v_valor) = 'string' then v_valor #>> '{}' end;
    if v_texto is null
       or v_texto not in (new.id::text || '/logo.png', new.id::text || '/logo.jpg', new.id::text || '/logo.webp') then
      raise exception 'settings.logo_ruta debe ser <tenant>/logo.png, .jpg o .webp del propio tenant'
        using errcode = 'check_violation';
    end if;
  end if;
  return new;
end
$$;
