-- Validación de tenants.settings de la web de reservas: cancelacion_plazo_horas (entero 0-720), color_principal y
-- color_secundario ('#RRGGBB') y logo_ruta ('<id del tenant>/logo-<id>.png|jpg|webp', solo el propio tenant; el nombre único lo cubre el 75).
-- Cada rechazo se afirma por el código 23514 (check_violation).
begin;
select plan(82);

select lives_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000000', 'M', 'm0', '{"cancelacion_plazo_horas": 0}') $$, 'cancelacion_plazo_horas = 0: válido');
select lives_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000001', 'M', 'm1', '{"cancelacion_plazo_horas": 24}') $$, 'cancelacion_plazo_horas = 24: válido');
select lives_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000002', 'M', 'm2', '{"cancelacion_plazo_horas": 720}') $$, 'cancelacion_plazo_horas = 720: válido');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000003', 'M', 'm3', '{"cancelacion_plazo_horas": -1}') $$, '23514', null, 'cancelacion_plazo_horas = -1: rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000004', 'M', 'm4', '{"cancelacion_plazo_horas": 721}') $$, '23514', null, 'cancelacion_plazo_horas = 721: rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000005', 'M', 'm5', '{"cancelacion_plazo_horas": 24.5}') $$, '23514', null, 'cancelacion_plazo_horas = 24.5: rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000006', 'M', 'm6', '{"cancelacion_plazo_horas": "24"}') $$, '23514', null, 'cancelacion_plazo_horas = "24": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000007', 'M', 'm7', '{"cancelacion_plazo_horas": null}') $$, '23514', null, 'cancelacion_plazo_horas = null: rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000008', 'M', 'm8', '{"cancelacion_plazo_horas": true}') $$, '23514', null, 'cancelacion_plazo_horas = true: rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000009', 'M', 'm9', '{"cancelacion_plazo_horas": -5}') $$, '23514', null, 'cancelacion_plazo_horas = -5: rechazado');
select lives_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000010', 'M', 'm10', '{"color_principal": "#000000"}') $$, 'color_principal = "#000000": válido');
select lives_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000011', 'M', 'm11', '{"color_principal": "#FFFFFF"}') $$, 'color_principal = "#FFFFFF": válido');
select lives_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000012', 'M', 'm12', '{"color_principal": "#a1B2c3"}') $$, 'color_principal = "#a1B2c3": válido');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000013', 'M', 'm13', '{"color_principal": "000000"}') $$, '23514', null, 'color_principal = "000000": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000014', 'M', 'm14', '{"color_principal": "#FFF"}') $$, '23514', null, 'color_principal = "#FFF": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000015', 'M', 'm15', '{"color_principal": "#12345"}') $$, '23514', null, 'color_principal = "#12345": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000016', 'M', 'm16', '{"color_principal": "#1234567"}') $$, '23514', null, 'color_principal = "#1234567": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000017', 'M', 'm17', '{"color_principal": "#GGGGGG"}') $$, '23514', null, 'color_principal = "#GGGGGG": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000018', 'M', 'm18', '{"color_principal": "#12345678"}') $$, '23514', null, 'color_principal = "#12345678": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000019', 'M', 'm19', '{"color_principal": "#fff<script>"}') $$, '23514', null, 'color_principal = "#fff<script>": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000020', 'M', 'm20', '{"color_principal": "<script>alert(1)</script>"}') $$, '23514', null, 'color_principal = "<script>alert(1)</script>": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000021', 'M', 'm21', '{"color_principal": "red"}') $$, '23514', null, 'color_principal = "red": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000022', 'M', 'm22', '{"color_principal": ""}') $$, '23514', null, 'color_principal = "": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000023', 'M', 'm23', '{"color_principal": "#112233\n"}') $$, '23514', null, 'color_principal = "#112233\n": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000024', 'M', 'm24', '{"color_principal": " #112233"}') $$, '23514', null, 'color_principal = " #112233": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000025', 'M', 'm25', '{"color_principal": 123456}') $$, '23514', null, 'color_principal = 123456: rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000026', 'M', 'm26', '{"color_principal": null}') $$, '23514', null, 'color_principal = null: rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000027', 'M', 'm27', '{"color_principal": true}') $$, '23514', null, 'color_principal = true: rechazado');
select lives_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000028', 'M', 'm28', '{"color_secundario": "#000000"}') $$, 'color_secundario = "#000000": válido');
select lives_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000029', 'M', 'm29', '{"color_secundario": "#FFFFFF"}') $$, 'color_secundario = "#FFFFFF": válido');
select lives_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000030', 'M', 'm30', '{"color_secundario": "#a1B2c3"}') $$, 'color_secundario = "#a1B2c3": válido');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000031', 'M', 'm31', '{"color_secundario": "000000"}') $$, '23514', null, 'color_secundario = "000000": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000032', 'M', 'm32', '{"color_secundario": "#FFF"}') $$, '23514', null, 'color_secundario = "#FFF": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000033', 'M', 'm33', '{"color_secundario": "#12345"}') $$, '23514', null, 'color_secundario = "#12345": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000034', 'M', 'm34', '{"color_secundario": "#1234567"}') $$, '23514', null, 'color_secundario = "#1234567": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000035', 'M', 'm35', '{"color_secundario": "#GGGGGG"}') $$, '23514', null, 'color_secundario = "#GGGGGG": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000036', 'M', 'm36', '{"color_secundario": "#12345678"}') $$, '23514', null, 'color_secundario = "#12345678": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000037', 'M', 'm37', '{"color_secundario": "#fff<script>"}') $$, '23514', null, 'color_secundario = "#fff<script>": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000038', 'M', 'm38', '{"color_secundario": "<script>alert(1)</script>"}') $$, '23514', null, 'color_secundario = "<script>alert(1)</script>": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000039', 'M', 'm39', '{"color_secundario": "red"}') $$, '23514', null, 'color_secundario = "red": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000040', 'M', 'm40', '{"color_secundario": ""}') $$, '23514', null, 'color_secundario = "": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000041', 'M', 'm41', '{"color_secundario": "#112233\n"}') $$, '23514', null, 'color_secundario = "#112233\n": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000042', 'M', 'm42', '{"color_secundario": " #112233"}') $$, '23514', null, 'color_secundario = " #112233": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000043', 'M', 'm43', '{"color_secundario": 123456}') $$, '23514', null, 'color_secundario = 123456: rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000044', 'M', 'm44', '{"color_secundario": null}') $$, '23514', null, 'color_secundario = null: rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000045', 'M', 'm45', '{"color_secundario": true}') $$, '23514', null, 'color_secundario = true: rechazado');
select lives_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000046', 'M', 'm46', '{"logo_ruta": "b0000000-0000-0000-0000-000000000046/logo-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.png"}') $$, 'logo_ruta = "<tenant>/logo-<id>.png": válido');
select lives_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000047', 'M', 'm47', '{"logo_ruta": "b0000000-0000-0000-0000-000000000047/logo-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg"}') $$, 'logo_ruta = "<tenant>/logo-<id>.jpg": válido');
select lives_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000048', 'M', 'm48', '{"logo_ruta": "b0000000-0000-0000-0000-000000000048/logo-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.webp"}') $$, 'logo_ruta = "<tenant>/logo-<id>.webp": válido');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000049', 'M', 'm49', '{"logo_ruta": "b0000000-0000-0000-0000-000000000049/logo.svg"}') $$, '23514', null, 'logo_ruta = "<tenant>/logo.svg": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000050', 'M', 'm50', '{"logo_ruta": "b0000000-0000-0000-0000-000000000050/logo.gif"}') $$, '23514', null, 'logo_ruta = "<tenant>/logo.gif": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000051', 'M', 'm51', '{"logo_ruta": "b0000000-0000-0000-0000-000000000051/logo.PNG"}') $$, '23514', null, 'logo_ruta = "<tenant>/logo.PNG": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000052', 'M', 'm52', '{"logo_ruta": "b0000000-0000-0000-0000-000000000052/../logo.png"}') $$, '23514', null, 'logo_ruta = "<tenant>/../logo.png": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000053', 'M', 'm53', '{"logo_ruta": "b0000000-0000-0000-0000-000000000053/x/../logo.png"}') $$, '23514', null, 'logo_ruta = "<tenant>/x/../logo.png": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000054', 'M', 'm54', '{"logo_ruta": "b0000000-0000-0000-0000-000000000054/sub/logo.png"}') $$, '23514', null, 'logo_ruta = "<tenant>/sub/logo.png": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000055', 'M', 'm55', '{"logo_ruta": "/b0000000-0000-0000-0000-000000000055/logo.png"}') $$, '23514', null, 'logo_ruta = "/<tenant>/logo.png": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000056', 'M', 'm56', '{"logo_ruta": "b0000000-0000-0000-0000-000000000056/logo.png/"}') $$, '23514', null, 'logo_ruta = "<tenant>/logo.png/": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000057', 'M', 'm57', '{"logo_ruta": "b0000000-0000-0000-0000-000000000057/logo.png.exe"}') $$, '23514', null, 'logo_ruta = "<tenant>/logo.png.exe": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000058', 'M', 'm58', '{"logo_ruta": "b0000000-0000-0000-0000-000000000058/otro.png"}') $$, '23514', null, 'logo_ruta = "<tenant>/otro.png": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000059', 'M', 'm59', '{"logo_ruta": "http://b0000000-0000-0000-0000-000000000059/logo.png"}') $$, '23514', null, 'logo_ruta = "http://<tenant>/logo.png": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000060', 'M', 'm60', '{"logo_ruta": "https://x.com/logo.png"}') $$, '23514', null, 'logo_ruta = "https://x.com/logo.png": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000061', 'M', 'm61', '{"logo_ruta": "logo.png"}') $$, '23514', null, 'logo_ruta = "logo.png": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000062', 'M', 'm62', '{"logo_ruta": ""}') $$, '23514', null, 'logo_ruta = "": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000063', 'M', 'm63', '{"logo_ruta": "00000000-0000-0000-0000-0000000000ff/logo.png"}') $$, '23514', null, 'logo_ruta = "00000000-0000-0000-0000-0000000000ff/logo.png": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000064', 'M', 'm64', '{"logo_ruta": "00000000-0000-0000-0000-0000000000ff/../b0000000-0000-0000-0000-000000000064/logo.png"}') $$, '23514', null, 'logo_ruta = "00000000-0000-0000-0000-0000000000ff/../<tenant>/logo.png": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000065', 'M', 'm65', '{"logo_ruta": "b0000000-0000-0000-0000-000000000065\\logo.png"}') $$, '23514', null, 'logo_ruta = "<tenant>\\logo.png": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000066', 'M', 'm66', '{"logo_ruta": "b0000000-0000-0000-0000-000000000066/logo.png\n"}') $$, '23514', null, 'logo_ruta = "<tenant>/logo.png\n": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000067', 'M', 'm67', '{"logo_ruta": "b0000000-0000-0000-0000-000000000067/logo.png "}') $$, '23514', null, 'logo_ruta = "<tenant>/logo.png ": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000068', 'M', 'm68', '{"logo_ruta": "b0000000-0000-0000-0000-000000000068/"}') $$, '23514', null, 'logo_ruta = "<tenant>/": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000069', 'M', 'm69', '{"logo_ruta": "b0000000-0000-0000-0000-000000000069"}') $$, '23514', null, 'logo_ruta = "<tenant>": rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000070', 'M', 'm70', '{"logo_ruta": 123}') $$, '23514', null, 'logo_ruta = 123: rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000071', 'M', 'm71', '{"logo_ruta": null}') $$, '23514', null, 'logo_ruta = null: rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000072', 'M', 'm72', '{"logo_ruta": true}') $$, '23514', null, 'logo_ruta = true: rechazado');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('b0000000-0000-0000-0000-000000000073', 'M', 'm73', '{"logo_ruta": ["b0000000-0000-0000-0000-000000000073/logo.png"]}') $$, '23514', null, 'logo_ruta = ["<tenant>/logo.png"]: rechazado');

-- Un tenant que existe: el UPDATE valida igual que el INSERT, con el id real de la fila.
insert into public.tenants (id, name, slug) values ('00000000-0000-0000-0000-0000000000b1', 'B', 'marca-b');
select lives_ok(
  $$update public.tenants set settings = '{"logo_ruta": "00000000-0000-0000-0000-0000000000b1/logo.webp", "color_principal": "#112233", "color_secundario": "#aabbcc", "cancelacion_plazo_horas": 48}' where id = '00000000-0000-0000-0000-0000000000b1'$$,
  'un UPDATE con las cuatro claves válidas es aceptado');
select throws_ok(
  $$update public.tenants set settings = '{"cancelacion_plazo_horas": 721}' where id = '00000000-0000-0000-0000-0000000000b1'$$,
  '23514', null, 'un UPDATE con un plazo fuera de rango es rechazado');
select throws_ok(
  $$update public.tenants set settings = '{"logo_ruta": "00000000-0000-0000-0000-0000000000ff/logo.png"}' where id = '00000000-0000-0000-0000-0000000000b1'$$,
  '23514', null, 'un UPDATE con el logo de otro tenant es rechazado');
select is(
  (select settings ->> 'cancelacion_plazo_horas' from public.tenants where id = '00000000-0000-0000-0000-0000000000b1'),
  '48', 'tras los rechazos queda el último valor válido');

-- Compatibilidad: un settings anterior (sin claves nuevas) y la ausencia de las claves siguen validando.
select lives_ok(
  $$update public.tenants set settings = '{"booking_approval_timeout_hours": 24, "booking_max_active_per_customer": 5, "booking_max_per_hour": 3, "booking_min_notice_minutes": 30, "local_direccion": "Calle 1", "otra": {"a": [1]}}' where id = '00000000-0000-0000-0000-0000000000b1'$$,
  'un settings anterior, sin las claves nuevas, sigue siendo válido');
select lives_ok(
  $$update public.tenants set settings = '{}' where id = '00000000-0000-0000-0000-0000000000b1'$$,
  'un settings vacío es válido');
select throws_ok(
  $$update public.tenants set settings = '{"booking_max_per_hour": 61}' where id = '00000000-0000-0000-0000-0000000000b1'$$,
  '23514', null, 'las reglas anteriores siguen vigentes');
select throws_ok(
  $$update public.tenants set settings = '{"cancelacion_plazo_horas": 24, "color_principal": "rojo"}' where id = '00000000-0000-0000-0000-0000000000b1'$$,
  '23514', null, 'una clave inválida junto a una válida rechaza el settings');

select * from finish();
rollback;
