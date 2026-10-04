-- Test de humo: confirma que pgTAP corre en Supabase local y en CI.
begin;
select plan(1);
select ok(true, 'pgTAP funciona');
select * from finish();
rollback;
