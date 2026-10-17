"""Fixtures compartidas de los tests de integración contra Postgres (Supabase local).

Los tests marcados `integracion` necesitan `DATABASE_URL` apuntando a una base con las
migraciones aplicadas (local: `supabase start` -> postgresql://postgres:postgres@127.0.0.1:54322/postgres).
Sin `DATABASE_URL` se saltan solos (skip, no fallo).

Las contraseñas de los roles de login no se versionan: al empezar la sesión de tests se
generan al azar (o se toman de `INTEGRACION_ROLES_PASSWORD`, si se quiere fijar una) y se
asignan con `alter role` usando el superusuario de test. Solo sirven para esa base local.
"""

import asyncio
import os
import secrets
from urllib.parse import quote, urlsplit, urlunsplit

import pytest

ROLES_LOGIN = (
    "bot_service",
    "dashboard_service",
    "platform_admin",
    "platform_admin_readonly",
    "purge_job",
    "scheduler_job",
)


def pytest_collection_modifyitems(config, items):
    if os.environ.get("DATABASE_URL"):
        return
    saltar = pytest.mark.skip(reason="DATABASE_URL no definida: tests de integración omitidos")
    for item in items:
        if "integracion" in item.keywords:
            item.add_marker(saltar)


def _dsn_para_rol(dsn_base: str, rol: str, password: str) -> str:
    partes = urlsplit(dsn_base)
    host = partes.hostname or "127.0.0.1"
    if ":" in host:
        host = f"[{host}]"
    netloc = f"{quote(rol, safe='')}:{quote(password, safe='')}@{host}"
    if partes.port:
        netloc += f":{partes.port}"
    return urlunsplit((partes.scheme, netloc, partes.path, partes.query, ""))


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL no definida")
    return url


@pytest.fixture(scope="session")
def roles_password(database_url: str) -> str:
    """Contraseña efímera de test, asignada a los 6 roles de login al empezar la sesión."""
    password = os.environ.get("INTEGRACION_ROLES_PASSWORD") or secrets.token_urlsafe(24)

    async def _asignar() -> None:
        import asyncpg

        conn = await asyncpg.connect(database_url)
        try:
            for rol in ROLES_LOGIN:
                # alter role no admite parámetros: la contraseña se escapa como literal.
                literal = password.replace("'", "''")
                await conn.execute(f"alter role {rol} with password '{literal}'")
        finally:
            await conn.close()

    asyncio.run(_asignar())
    return password


@pytest.fixture
async def db_superuser(database_url: str):
    """Conexión asyncpg como superusuario de test (el de DATABASE_URL)."""
    import asyncpg

    conn = await asyncpg.connect(database_url)
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def conectar_como(database_url: str, roles_password: str):
    """Fábrica: `conn = await conectar_como("dashboard_service")`. Cierra todo al terminar."""
    import asyncpg

    abiertas: list[asyncpg.Connection] = []

    async def _conectar(rol: str) -> asyncpg.Connection:
        if rol not in ROLES_LOGIN:
            raise ValueError(f"Rol de login desconocido: {rol}")
        conn = await asyncpg.connect(_dsn_para_rol(database_url, rol, roles_password))
        abiertas.append(conn)
        return conn

    try:
        yield _conectar
    finally:
        for conn in abiertas:
            await conn.close()


@pytest.fixture
async def dsn_servidor_mudo():
    """DSN de un servidor TCP que acepta conexiones y nunca responde (simula una base congelada). Sin DB real."""
    conexiones: list[asyncio.StreamWriter] = []

    async def _acepta_y_calla(_reader, writer):
        conexiones.append(writer)  # se mantiene abierta; no se lee ni se escribe nada

    servidor = await asyncio.start_server(_acepta_y_calla, "127.0.0.1", 0)
    puerto = servidor.sockets[0].getsockname()[1]
    try:
        yield f"postgresql://u:p@127.0.0.1:{puerto}/db"
    finally:
        for w in conexiones:
            w.close()
        servidor.close()
