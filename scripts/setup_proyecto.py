"""Setup inicial de un proyecto Supabase (ARCHITECTURE.md §2.10, §2.3).

Hace, de forma idempotente y en una sola transacción:
  1. Inserta la primera fila de `platform_admins` para una cuenta que ya existe en Auth (la cuenta
     la crea el dueño del proyecto antes; este script no crea cuentas de Auth).
  2. Siembra en Vault la clave HMAC `channel_user_hmac_key` que usa `pseudonymize_channel_user`.
     Se genera al azar solo si no existe: si ya existe NO se rota (rotarla rompería el recontacto
     de clientes anonimizados, §2.3). La clave nunca se imprime ni se loguea.
  3. Opcional: asigna la contraseña de los 6 roles de login.

Conexión: corre con el rol dueño de las migraciones (conexión directa a Postgres), no con
`service_role`, que no tiene permisos de tabla sobre `public`. La URL va en la variable de entorno
`SETUP_DATABASE_URL` (nunca por argumento de línea de comandos).

Uso:
    SETUP_DATABASE_URL=postgresql://... python scripts/setup_proyecto.py --email dueno@ejemplo.com
    python scripts/setup_proyecto.py --auth-user-id <uuid>
    python scripts/setup_proyecto.py --email ... --pedir-passwords   # pide cada contraseña (sin eco)

Contraseñas de roles: variables `SETUP_PASSWORD_<ROL>` en mayúsculas (p. ej.
`SETUP_PASSWORD_BOT_SERVICE`); con `--pedir-passwords` se piden por prompt las que falten. Nunca por
argumento ni en archivos versionados.

Pendiente a propósito (diferido con la elección del proveedor de WhatsApp): sembrar en Vault el App
Secret de WhatsApp de producción. Hoy se hace a mano en Vault; no es parte de este script.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import secrets
import sys
import uuid
from collections.abc import Mapping, Sequence

CLAVE_HMAC = "channel_user_hmac_key"
ROLES_LOGIN = (
    "bot_service",
    "dashboard_service",
    "platform_admin",
    "platform_admin_readonly",
    "purge_job",
    "scheduler_job",
)
PREFIJO_VAR_ROL = "SETUP_PASSWORD_"
VAR_URL = "SETUP_DATABASE_URL"


class ErrorSetup(Exception):
    """Error esperado: el mensaje es claro y se muestra tal cual al operador."""


def passwords_desde_entorno(env: Mapping[str, str]) -> dict[str, str]:
    """Contraseñas de roles definidas en el entorno (ignora variables vacías)."""
    return {rol: env[PREFIJO_VAR_ROL + rol.upper()] for rol in ROLES_LOGIN if env.get(PREFIJO_VAR_ROL + rol.upper())}


def _parsear(argv: Sequence[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Setup inicial: primer platform_admin y clave HMAC en Vault.")
    destino = p.add_mutually_exclusive_group()
    destino.add_argument("--email", help="email de una cuenta ya existente en Auth")
    destino.add_argument("--auth-user-id", help="id (UUID) de una cuenta ya existente en Auth")
    p.add_argument(
        "--pedir-passwords",
        action="store_true",
        help="pide por prompt (sin eco) la contraseña de los roles que no vengan por entorno",
    )
    return p.parse_args(argv)


async def _aplicar(conn, args, passwords: dict[str, str]) -> list[str]:
    """Todo en una transacción: o queda completo o no queda nada."""
    hechos: list[str] = []
    async with conn.transaction():
        await conn.execute("select pg_advisory_xact_lock(hashtextextended('setup_proyecto', 0))")

        if args.auth_user_id:
            uid = await conn.fetchval("select id from auth.users where id = $1", uuid.UUID(args.auth_user_id))
        else:
            uid = await conn.fetchval("select id from auth.users where lower(email) = lower($1)", args.email)
        if uid is None:
            raise ErrorSetup(
                "No existe esa cuenta en Auth: creala primero (Authentication > Users) y volvé a correr el script."
            )

        insertado = await conn.fetchval(
            "insert into public.platform_admins (auth_user_id) values ($1) "
            "on conflict (auth_user_id) do nothing returning true",
            uid,
        )
        hechos.append("platform_admin creado" if insertado else "platform_admin ya existía")

        existe = await conn.fetchval("select 1 from vault.secrets where name = $1", CLAVE_HMAC)
        if existe:
            hechos.append("clave HMAC ya existía (no se rota)")
        else:
            await conn.fetchval(
                "select vault.create_secret($1, $2, $3)",
                secrets.token_urlsafe(48),
                CLAVE_HMAC,
                "Clave HMAC de seudonimización de identificadores de canal",
            )
            hechos.append("clave HMAC sembrada en Vault")

        for rol, password in passwords.items():
            # El escape lo hace el servidor (%L): la contraseña nunca se concatena en el cliente.
            sentencia = await conn.fetchval(
                "select format('alter role %I with password %L', $1::text, $2::text)", rol, password
            )
            await conn.execute(sentencia)
        if passwords:
            hechos.append(f"contraseña asignada a {len(passwords)} rol(es) de login")
    return hechos


async def ejecutar(argv: Sequence[str], env: Mapping[str, str]) -> int:
    args = _parsear(argv)
    try:
        if not args.email and not args.auth_user_id:
            raise ErrorSetup("Indicá la cuenta del primer platform_admin con --email o --auth-user-id.")
        if args.auth_user_id:
            try:
                uuid.UUID(args.auth_user_id)
            except ValueError:
                raise ErrorSetup("--auth-user-id no es un UUID válido.") from None
        url = env.get(VAR_URL)
        if not url:
            raise ErrorSetup(f"Falta la variable de entorno {VAR_URL} (conexión directa a Postgres como dueño).")

        passwords = passwords_desde_entorno(env)
        if args.pedir_passwords:
            for rol in ROLES_LOGIN:
                if rol not in passwords:
                    valor = getpass.getpass(f"Contraseña para {rol} (vacío = no cambiar): ")
                    if valor:
                        passwords[rol] = valor

        import asyncpg

        try:
            conn = await asyncpg.connect(url)
        except Exception as exc:  # el mensaje de la librería puede incluir datos de la URL: solo el tipo
            raise ErrorSetup(f"No se pudo conectar a Postgres ({type(exc).__name__}). Revisá {VAR_URL}.") from None
        try:
            try:
                hechos = await _aplicar(conn, args, passwords)
            except ErrorSetup:
                raise
            except asyncpg.PostgresError as exc:
                raise ErrorSetup(f"La base rechazó el setup ({type(exc).__name__}): {exc.message}") from None
        finally:
            await conn.close()
    except ErrorSetup as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    for hecho in hechos:
        print(f"- {hecho}")
    return 0


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    return asyncio.run(ejecutar(sys.argv[1:] if argv is None else argv, os.environ if env is None else env))


if __name__ == "__main__":
    sys.exit(main())
