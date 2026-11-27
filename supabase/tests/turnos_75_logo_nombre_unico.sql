-- logo_ruta con nombre único por subida ('<id del tenant>/logo-<32 hexadecimales>.png|jpg|webp'): el trigger
-- tenants_validar_settings acepta solo el nombre nuevo del propio tenant y rechaza el anterior y cualquier ruta que
-- escape de su carpeta.
begin;
select plan(15);

select lives_ok($$ insert into public.tenants (id, name, slug, settings) values ('c0000000-0000-0000-0000-000000007501', 'M', 'm750', '{"logo_ruta": "c0000000-0000-0000-0000-000000007501/logo-0123456789abcdef0123456789abcdef.png"}') $$, 'logo_ruta válido: nombre único png');
select lives_ok($$ insert into public.tenants (id, name, slug, settings) values ('c0000000-0000-0000-0000-000000007502', 'M', 'm751', '{"logo_ruta": "c0000000-0000-0000-0000-000000007502/logo-0123456789abcdef0123456789abcdef.jpg"}') $$, 'logo_ruta válido: nombre único jpg');
select lives_ok($$ insert into public.tenants (id, name, slug, settings) values ('c0000000-0000-0000-0000-000000007503', 'M', 'm752', '{"logo_ruta": "c0000000-0000-0000-0000-000000007503/logo-0123456789abcdef0123456789abcdef.webp"}') $$, 'logo_ruta válido: nombre único webp');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('c0000000-0000-0000-0000-000000007504', 'M', 'm753', '{"logo_ruta": "c0000000-0000-0000-0000-000000007504/logo.png"}') $$, '23514', null, 'logo_ruta rechazado: nombre sin id');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('c0000000-0000-0000-0000-000000007505', 'M', 'm754', '{"logo_ruta": "c0000000-0000-0000-0000-000000007505/logo-0123456789ABCDEF0123456789ABCDEF.png"}') $$, '23514', null, 'logo_ruta rechazado: id en mayúscula');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('c0000000-0000-0000-0000-000000007506', 'M', 'm755', '{"logo_ruta": "c0000000-0000-0000-0000-000000007506/logo-0123456789abcdef0123456789abcde.png"}') $$, '23514', null, 'logo_ruta rechazado: id de 31 caracteres');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('c0000000-0000-0000-0000-000000007507', 'M', 'm756', '{"logo_ruta": "c0000000-0000-0000-0000-000000007507/logo-0123456789abcdef0123456789abcdef0.png"}') $$, '23514', null, 'logo_ruta rechazado: id de 33 caracteres');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('c0000000-0000-0000-0000-000000007508', 'M', 'm757', '{"logo_ruta": "c0000000-0000-0000-0000-000000007508/logo-0123456789abcdef0123456789abcdef.svg"}') $$, '23514', null, 'logo_ruta rechazado: svg');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('c0000000-0000-0000-0000-000000007509', 'M', 'm758', '{"logo_ruta": "c0000000-0000-0000-0000-000000007509/logo-0123456789abcdef0123456789abcdef.png/"}') $$, '23514', null, 'logo_ruta rechazado: barra final');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('c0000000-0000-0000-0000-000000007510', 'M', 'm759', '{"logo_ruta": "c0000000-0000-0000-0000-000000007510/sub/logo-0123456789abcdef0123456789abcdef.png"}') $$, '23514', null, 'logo_ruta rechazado: subcarpeta');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('c0000000-0000-0000-0000-000000007511', 'M', 'm7510', '{"logo_ruta": "c0000000-0000-0000-0000-000000007511/../logo-0123456789abcdef0123456789abcdef.png"}') $$, '23514', null, 'logo_ruta rechazado: sale de la carpeta');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('c0000000-0000-0000-0000-000000007512', 'M', 'm7511', '{"logo_ruta": "c0000000-0000-0000-0000-000000007599/logo-0123456789abcdef0123456789abcdef.png"}') $$, '23514', null, 'logo_ruta rechazado: logo de otro tenant');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('c0000000-0000-0000-0000-000000007513', 'M', 'm7512', '{"logo_ruta": "/c0000000-0000-0000-0000-000000007513/logo-0123456789abcdef0123456789abcdef.png"}') $$, '23514', null, 'logo_ruta rechazado: barra inicial');
select throws_ok($$ insert into public.tenants (id, name, slug, settings) values ('c0000000-0000-0000-0000-000000007514', 'M', 'm7513', '{"logo_ruta": "https://x.com/logo-0123456789abcdef0123456789abcdef.png"}') $$, '23514', null, 'logo_ruta rechazado: url');
insert into public.tenants (id, name, slug, settings) values ('c0000000-0000-0000-0000-000000007550', 'M', 'm75x', '{}');
select throws_ok($$ update public.tenants set settings = '{"logo_ruta": "c0000000-0000-0000-0000-000000007550/logo.png"}' where id = 'c0000000-0000-0000-0000-000000007550' $$, '23514', null, 'logo_ruta sin id: el UPDATE también se rechaza');
select * from finish();
rollback;
