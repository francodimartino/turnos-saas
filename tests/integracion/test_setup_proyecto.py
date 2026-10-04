"""Script de setup inicial contra Supabase local: primer platform_admin y clave HMAC en Vault (§2.10, §2.3).

El script corre con el rol dueño de las migraciones (el de DATABASE_URL en test). Ningún secreto
literal: el usuario de Auth y las contraseñas se generan en cada corrida.
"""

import importlib.util
import secrets
import uuid
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import pytest

pytestmark = pytest.mark.integracion

asyncpg = pytest.importorskip("asyncpg")

RUTA = Path(__file__).resolve().parents[2] / "scripts" / "setup_proyecto.py"
CLAVE = "channel_user_hmac_key"


@pytest.fixture(scope="module")
def setup():
    spec = importlib.util.spec_from_file_location("setup_proyecto", RUTA)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture
async def estado_limpio(db_superuser):
    """Aparta la clave HMAC preexistente (si la hay) y restaura el estado al terminar."""
    previa = await db_superuser.fetchrow(
        "select id, decrypted_secret from vault.decrypted_secrets where name = $1", CLAVE
    )
    if previa:
        await db_superuser.execute("delete from vault.secrets where id = $1", previa["id"])
    try:
        yield
    finally:
        await db_superuser.execute("delete from vault.secrets where name = $1", CLAVE)
        if previa:
            await db_superuser.fetchval("select vault.create_secret($1, $2)", previa["decrypted_secret"], CLAVE)


@pytest.fixture
async def usuario_auth(db_superuser):
    uid = uuid.uuid4()
    email = f"setup-{uid.hex[:10]}@example.com"
    await db_superuser.execute("insert into auth.users (id, email) values ($1, $2)", uid, email)
    try:
        yield {"id": uid, "email": email}
    finally:
        async with db_superuser.transaction():
            # el trigger impide borrar el último platform_admin: solo se desactiva en la limpieza
            await db_superuser.execute(
                "alter table public.platform_admins disable trigger platform_admins_proteger_ultimo"
            )
            await db_superuser.execute("delete from public.platform_admins where auth_user_id = $1", uid)
            await db_superuser.execute("delete from auth.users where id = $1", uid)
            await db_superuser.execute(
                "alter table public.platform_admins enable trigger platform_admins_proteger_ultimo"
            )


def entorno(database_url):
    return {"SETUP_DATABASE_URL": database_url}


async def clave_en_vault(conn):
    return await conn.fetchval("select decrypted_secret from vault.decrypted_secrets where name = $1", CLAVE)


async def test_crea_admin_y_clave_y_es_idempotente(
    setup, database_url, db_superuser, estado_limpio, usuario_auth, capsys
):
    argv = ["--email", usuario_auth["email"].upper()]
    assert await setup.ejecutar(argv, entorno(database_url)) == 0
    clave1 = await clave_en_vault(db_superuser)
    assert clave1 and len(clave1) >= 32
    sql_admin = "select count(*) from public.platform_admins where auth_user_id = $1"
    assert await db_superuser.fetchval(sql_admin, usuario_auth["id"]) == 1

    assert await setup.ejecutar(argv, entorno(database_url)) == 0
    assert await clave_en_vault(db_superuser) == clave1  # no rota
    assert await db_superuser.fetchval("select count(*) from vault.secrets where name = $1", CLAVE) == 1
    assert await db_superuser.fetchval(sql_admin, usuario_auth["id"]) == 1
    salida = capsys.readouterr()
    assert clave1 not in salida.out + salida.err


async def test_acepta_auth_user_id(setup, database_url, db_superuser, estado_limpio, usuario_auth):
    rc = await setup.ejecutar(["--auth-user-id", str(usuario_auth["id"])], entorno(database_url))
    assert rc == 0
    assert await db_superuser.fetchval(
        "select 1 from public.platform_admins where auth_user_id = $1", usuario_auth["id"]
    )


async def test_usuario_de_auth_inexistente_falla_sin_efectos(
    setup, database_url, db_superuser, estado_limpio, capsys
):
    rc = await setup.ejecutar(["--email", f"nadie-{uuid.uuid4().hex}@example.com"], entorno(database_url))
    assert rc == 1
    assert "Auth" in capsys.readouterr().err
    assert await clave_en_vault(db_superuser) is None  # nada a medias


async def test_seudonimizacion_y_anonimizacion_de_punta_a_punta(
    setup, database_url, db_superuser, estado_limpio, usuario_auth
):
    tenant, sufijo = uuid.uuid4(), uuid.uuid4().hex[:8]
    await db_superuser.execute(
        "insert into public.tenants (id, name, slug) values ($1, 'T', $2)", tenant, f"it-setup-{sufijo}"
    )
    seudonimo = "select public.pseudonymize_channel_user($1, 'telegram', 'x')"
    try:
        assert await db_superuser.fetchval(seudonimo, tenant) is None
        assert await setup.ejecutar(["--email", usuario_auth["email"]], entorno(database_url)) == 0
        assert await db_superuser.fetchval(seudonimo, tenant) is not None

        cliente = uuid.uuid4()
        await db_superuser.execute(
            "insert into public.end_customers (id, tenant_id, channel, channel_user_id, phone, name) "
            "values ($1, $2, 'telegram', $3, '+5491100000000', 'Nombre')",
            cliente,
            tenant,
            f"tg-{sufijo}",
        )
        await db_superuser.execute("select public.anonymize_end_customer_core($1, 'system', null)", cliente)
        fila = await db_superuser.fetchrow(
            "select channel_user_id, channel_user_hash, name from public.end_customers where id = $1", cliente
        )
        assert fila["channel_user_id"] is None and fila["name"] is None
        assert fila["channel_user_hash"]
    finally:
        async with db_superuser.transaction():
            await db_superuser.execute("alter table public.audit_log disable trigger audit_log_solo_agregar_row")
            await db_superuser.execute("delete from public.audit_log where tenant_id = $1", tenant)
            await db_superuser.execute("alter table public.audit_log enable trigger audit_log_solo_agregar_row")
            await db_superuser.execute("delete from public.end_customers where tenant_id = $1", tenant)
            await db_superuser.execute("delete from public.tenants where id = $1", tenant)


async def test_setea_passwords_de_roles_desde_entorno(
    setup, database_url, db_superuser, estado_limpio, usuario_auth, roles_password
):
    nueva = secrets.token_urlsafe(18)
    env = entorno(database_url) | {"SETUP_PASSWORD_SCHEDULER_JOB": nueva}
    try:
        assert await setup.ejecutar(["--email", usuario_auth["email"]], env) == 0
        partes = urlsplit(database_url)
        netloc = f"scheduler_job:{quote(nueva, safe='')}@{partes.hostname}:{partes.port}"
        conn = await asyncpg.connect(urlunsplit((partes.scheme, netloc, partes.path, partes.query, "")))
        try:
            assert await conn.fetchval("select 1") == 1
        finally:
            await conn.close()
    finally:
        # restaura la contraseña común de la sesión de tests para no romper otros módulos
        sentencia = await db_superuser.fetchval(
            "select format('alter role %I with password %L', $1::text, $2::text)", "scheduler_job", roles_password
        )
        await db_superuser.execute(sentencia)
