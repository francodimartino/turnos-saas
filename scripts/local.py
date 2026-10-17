"""Entorno local completo: Supabase local + los 3 backends, los 2 frontends, los 2 jobs y un simulador de Telegram.

Todo corre en Docker (Supabase con su CLI vía `npx`; los servicios Python en `python:3.12`, los frontends en `node`),
así que no depende del Python instalado en la máquina. Los valores son de PRUEBA y solo sirven en esta máquina.

Uso (desde la raíz del repo; solo necesita Python 3.10+, Docker Desktop y `npx`):
    python scripts/local.py up         # levanta todo y carga los datos de demo (idempotente)
    python scripts/local.py status     # qué está corriendo y las URLs
    python scripts/local.py down       # apaga todo (los datos se conservan)
    python scripts/local.py reset      # apaga, BORRA los datos locales y vuelve a levantar de cero
    python scripts/local.py purga      # corre el job de purga una vez (a pedido)
    python scripts/local.py clave <email>   # deja la contraseña de demo a una cuenta ya creada (sin pasar por el mail)
    python scripts/local.py vencer     # adelanta el vencimiento de los turnos pendientes (ver docs/local.md)
    python scripts/local.py logs [servicio]

Puertos propios (no pisan a otros proyectos Supabase): 55420-55424 (Supabase), 8110-8112 (backends), 8120 (simulador
de Telegram), 5173 y 5174 (frontends). Guía para no técnicos: docs/local.md.
"""

from __future__ import annotations

import argparse
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import quote

RAIZ = Path(__file__).resolve().parent.parent
DIR_LOCAL = RAIZ / ".local"
DIR_SUPABASE = DIR_LOCAL / "supabase"  # workdir de la CLI: <DIR_SUPABASE>/supabase/config.toml
ARCHIVO_ENV = RAIZ / ".env.local"
COMPOSE = RAIZ / "local" / "docker-compose.yml"
PROYECTO_COMPOSE = "turnos-local"
PROYECTO_SUPABASE = "turnos-saas-local"

# Puertos del host (los de la CLI por defecto son 543xx: acá se usan otros para convivir con otros proyectos).
P_SUPABASE_API = 55421
P_SUPABASE_DB = 55422
P_SUPABASE_STUDIO = 55423
P_SUPABASE_MAIL = 55424
P_SUPABASE_SHADOW = 55420
P_BOT = 8110
P_DASHBOARD_BACKEND = 8111
P_ADMIN_BACKEND = 8112
P_SIMULADOR = 8120
P_DASHBOARD_FRONTEND = 5173
P_ADMIN_FRONTEND = 5174

HOST_DOCKER = "host.docker.internal"  # cómo ve un contenedor a los puertos publicados en la máquina
ROLES = {
    "BOT_DATABASE_URL": "bot_service",
    "DASHBOARD_DATABASE_URL": "dashboard_service",
    "ADMIN_DATABASE_URL": "platform_admin",
    "ADMIN_READONLY_DATABASE_URL": "platform_admin_readonly",
    "PURGE_DATABASE_URL": "purge_job",
    "SCHEDULER_DATABASE_URL": "scheduler_job",
}
# Puertos que publica el compose: tienen que quedar solo en loopback. Los de Supabase (55420-55424) los publica la CLI.
PUERTOS_COMPOSE = (P_BOT, P_DASHBOARD_BACKEND, P_ADMIN_BACKEND, P_SIMULADOR, P_DASHBOARD_FRONTEND, P_ADMIN_FRONTEND)
PUERTOS_SUPABASE = tuple(range(55420, 55425))

# Contenedores de Supabase que no hacen falta para esta app (se omiten: arranca más rápido y gasta menos memoria).
SUPABASE_OMITIDOS = "realtime,storage-api,imgproxy,edge-runtime,logflare,vector,supavisor,postgrest"

# Usuarios de demo: SOLO locales. La contraseña se genera al azar la primera vez (.env.local) y `up` la imprime.
FRANCO_EMAIL = "franco@turnos.test"
OWNER_EMAIL = "duena@turnos.test"
STAFF_EMAIL = "recepcion@turnos.test"

URLS = {
    "Dashboard del negocio (owner / staff)": f"http://localhost:{P_DASHBOARD_FRONTEND}",
    "Panel de administración (Franco)": f"http://localhost:{P_ADMIN_FRONTEND}",
    "Simulador de Telegram": f"http://localhost:{P_SIMULADOR}",
    "Correos de Supabase (invitaciones)": f"http://localhost:{P_SUPABASE_MAIL}",
    "Supabase Studio (base de datos)": f"http://localhost:{P_SUPABASE_STUDIO}",
}


PLANTILLA_INVITACION = (
    "<h2>Te invitaron a Turnos</h2>\n"
    '<p><a href="{{ .SiteURL }}/invitacion?token_hash={{ .TokenHash }}&type=invite">Aceptar la invitación y elegir '
    "mi contraseña</a></p>\n"
)


class ErrorLocal(Exception):
    """Error esperado: el mensaje es claro y se muestra tal cual."""


# --- Supabase: configuración propia (puertos distintos) ---


def config_supabase_local(texto_repo: str) -> str:
    """Config de la CLI para el entorno local: la del repo con otro `project_id`, otros puertos, MFA y correo.

    Parte de `supabase/config.toml` (así el Auth Hook y el resto quedan igual que en CI) y solo cambia lo local.
    """
    puertos = {
        ("api", "port"): P_SUPABASE_API,
        ("db", "port"): P_SUPABASE_DB,
        ("db", "shadow_port"): P_SUPABASE_SHADOW,
        ("studio", "port"): P_SUPABASE_STUDIO,
    }
    salida: list[str] = []
    seccion = ""
    for linea in texto_repo.splitlines():
        m = re.match(r"\s*\[([^\]]+)\]\s*$", linea)
        if m:
            seccion = m.group(1)
        m = re.match(r"\s*(\w+)\s*=", linea)
        if m and (seccion, m.group(1)) in puertos:
            linea = f"{m.group(1)} = {puertos[(seccion, m.group(1))]}"
        elif m and not seccion and m.group(1) == "project_id":
            linea = f'project_id = "{PROYECTO_SUPABASE}"'
        elif m and seccion == "auth" and m.group(1) in ("site_url", "additional_redirect_urls"):
            valor = f'"http://localhost:{P_DASHBOARD_FRONTEND}"'
            linea = f"{m.group(1)} = {valor if m.group(1) == 'site_url' else '[' + valor + ']'}"
        salida.append(linea)
    texto = "\n".join(salida).rstrip() + "\n"
    extras = {
        "inbucket": f"enabled = true\nport = {P_SUPABASE_MAIL}\n",
        "auth.mfa.totp": "enroll_enabled = true\nverify_enabled = true\n",
        "analytics": "enabled = false\n",
        "db.pooler": "enabled = false\n",
        # El mail de invitación apunta a la pantalla `/invitacion?token_hash=...` del dashboard (como en producción).
        "auth.email.template.invite": (
            'subject = "Te invitaron a Turnos (entorno local)"\ncontent_path = "./supabase/templates/invite.html"\n'
        ),
    }
    for nombre, cuerpo in extras.items():
        if f"[{nombre}]" not in texto:
            texto += f"\n[{nombre}]\n{cuerpo}"
    return texto


def preparar_workdir_supabase() -> Path:
    """Crea `.local/supabase/supabase/` con la config local y una copia fresca de las migraciones."""
    destino = DIR_SUPABASE / "supabase"
    destino.mkdir(parents=True, exist_ok=True)
    texto = (RAIZ / "supabase" / "config.toml").read_text(encoding="utf-8")
    (destino / "config.toml").write_text(config_supabase_local(texto), encoding="utf-8")
    plantillas = destino / "templates"
    plantillas.mkdir(exist_ok=True)
    repo_plantilla = RAIZ / "supabase" / "templates" / "invite.html"
    contenido = repo_plantilla.read_text(encoding="utf-8") if repo_plantilla.exists() else PLANTILLA_INVITACION
    (plantillas / "invite.html").write_text(contenido, encoding="utf-8")
    migraciones = destino / "migrations"
    if migraciones.exists():
        shutil.rmtree(migraciones)
    shutil.copytree(RAIZ / "supabase" / "migrations", migraciones)
    return DIR_SUPABASE


def parsear_status_env(salida: str) -> dict[str, str]:
    """`supabase status -o env` -> {CLAVE: valor} (valores entre comillas dobles)."""
    datos: dict[str, str] = {}
    for linea in salida.splitlines():
        m = re.match(r'^([A-Z0-9_]+)="?(.*?)"?\s*$', linea.strip())
        if m:
            datos[m.group(1)] = m.group(2)
    return datos


# --- .env.local ------------------------------------------------------------------------------------------------------


def raices_python() -> list[str]:
    """Raíces de paquete de `pyproject.toml` (las mismas que usa pytest): van al PYTHONPATH de los contenedores."""
    texto = (RAIZ / "pyproject.toml").read_text(encoding="utf-8")
    bloque = texto.split("[tool.pytest.ini_options]")[1].split("pythonpath = [")[1].split("]")[0]
    return re.findall(r'"([^"]+)"', bloque)


def dependencias_python() -> list[str]:
    """`dependencies` de `pyproject.toml` (para la imagen de los servicios; el código va montado, no instalado)."""
    texto = (RAIZ / "pyproject.toml").read_text(encoding="utf-8")
    bloque = texto.split("dependencies = [")[1].split("\n]")[0]
    return re.findall(r'"([^"]+)"', bloque)


def generar_contrasena_demo(largo: int = 16) -> str:
    """Contraseña de demo que cumple SIEMPRE la política de Auth (minúscula, mayúscula, dígito y símbolo; 12+).

    Sin símbolos que rompan el .env o compose (`$`, `#`, comillas, espacios).
    """
    clases = ("abcdefghijklmnopqrstuvwxyz", "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "0123456789", "!@%^*_+-=")
    todos = "".join(clases)
    letras = [secrets.choice(c) for c in clases] + [secrets.choice(todos) for _ in range(largo - len(clases))]
    secrets.SystemRandom().shuffle(letras)
    return "".join(letras)


def _cumple_politica(c: str) -> bool:
    return (
        len(c) >= 12
        and any(x.islower() for x in c)
        and any(x.isupper() for x in c)
        and any(x.isdigit() for x in c)
        and any(not x.isalnum() for x in c)
    )


def generar_env(supabase: Mapping[str, str], previo: Mapping[str, str] | None = None) -> dict[str, str]:
    """Todas las variables locales, a partir de lo que informa Supabase local.

    Idempotente: lo que ya estaba en `previo` (contraseñas de roles, secretos) se conserva, así un `up` repetido no
    invalida los datos ya cargados. Nada de esto es secreto real: sirve solo contra esta base local.
    """
    previo = previo or {}

    def estable(clave: str, fabrica) -> str:
        return previo.get(clave) or fabrica()

    # Un .env anterior con una clave que no cumple la política de Auth (12+ y las cuatro clases) se regenera.
    demo_password = previo.get("DEMO_PASSWORD", "")
    if not _cumple_politica(demo_password):
        demo_password = generar_contrasena_demo()

    api_host = f"http://localhost:{P_SUPABASE_API}"
    # Los backends exigen que un SUPABASE_URL http sea de loopback: dentro del contenedor `localhost` apunta a la
    # máquina (local/docker-compose.yml lo agrega a /etc/hosts con `host-gateway`) y así se cumple sin tocar el código.
    api_contenedor = f"http://localhost:{P_SUPABASE_API}"
    env: dict[str, str] = {
        "LOCAL_PYTHONPATH": ":".join(f"/app/{r}" for r in raices_python()),
        "LOCAL_PYTHON_DEPENDENCIAS": " ".join(dependencias_python()),
        # Supabase local (lo informa la CLI)
        "SUPABASE_URL": api_contenedor,
        "SUPABASE_URL_HOST": api_host,
        "SUPABASE_ANON_KEY": supabase["ANON_KEY"],
        "SUPABASE_SERVICE_ROLE_KEY": supabase["SERVICE_ROLE_KEY"],
        "SUPABASE_JWKS_URL": f"{api_contenedor}/auth/v1/.well-known/jwks.json",
        "DB_OWNER_URL": f"postgresql://postgres:postgres@{HOST_DOCKER}:{P_SUPABASE_DB}/postgres",
        # Orígenes y cookies: todo es localhost (mismo sitio entre puertos), así que SameSite=Strict funciona; solo se
        # desactiva Secure porque es http.
        "DASHBOARD_ORIGIN": f"http://localhost:{P_DASHBOARD_FRONTEND}",
        "ADMIN_ORIGIN": f"http://localhost:{P_ADMIN_FRONTEND}",
        "VITE_API_URL_DASHBOARD": f"http://localhost:{P_DASHBOARD_BACKEND}",
        "VITE_API_URL_ADMIN": f"http://localhost:{P_ADMIN_BACKEND}",
        "COOKIE_SECURE": "false",
        "COOKIE_SAMESITE": "strict",
        # Telegram: el bot habla con el simulador en vez de api.telegram.org.
        # (vacío = el Telegram real: ver "Usar un bot real" en docs/local.md; `up` respeta el valor que dejes acá).
        "TELEGRAM_API_BASE_URL": previo["TELEGRAM_API_BASE_URL"]
        if "TELEGRAM_API_BASE_URL" in previo
        else "http://telegram-sim:8120",
        "SIM_BOT_URL": "http://bot:8000",
        "SENTRY_DSN": "",
        "VITE_SENTRY_DSN": "",
        "CALENDAR_OAUTH_STATE_SECRET": estable("CALENDAR_OAUTH_STATE_SECRET", lambda: secrets.token_urlsafe(32)),
        "GOOGLE_OAUTH_REDIRECT_URI": f"http://localhost:{P_DASHBOARD_FRONTEND}/calendar/callback",
        "INTERVALO_VENCIMIENTO_SEG": previo.get("INTERVALO_VENCIMIENTO_SEG") or "120",
        # Usuarios de demo
        "DEMO_FRANCO_EMAIL": FRANCO_EMAIL,
        "DEMO_OWNER_EMAIL": OWNER_EMAIL,
        "DEMO_STAFF_EMAIL": STAFF_EMAIL,
        "DEMO_PASSWORD": demo_password,
        # Puertos publicados (los lee el compose)
        "PUERTO_BOT": str(P_BOT),
        "PUERTO_DASHBOARD_BACKEND": str(P_DASHBOARD_BACKEND),
        "PUERTO_ADMIN_BACKEND": str(P_ADMIN_BACKEND),
        "PUERTO_SIMULADOR": str(P_SIMULADOR),
        "PUERTO_DASHBOARD_FRONTEND": str(P_DASHBOARD_FRONTEND),
        "PUERTO_ADMIN_FRONTEND": str(P_ADMIN_FRONTEND),
    }
    # La verificación del JWT usa JWKS: Supabase local firma los tokens de usuario con una clave asimétrica (ES256) y
    # publica su parte pública en /auth/v1/.well-known/jwks.json. No se define SUPABASE_JWT_SECRET (una u otra).
    for variable, rol in ROLES.items():
        password = estable(f"PASSWORD_{rol.upper()}", lambda: secrets.token_hex(16))
        env[f"PASSWORD_{rol.upper()}"] = password
        env[f"SETUP_PASSWORD_{rol.upper()}"] = password
        env[variable] = f"postgresql://{rol}:{quote(password)}@{HOST_DOCKER}:{P_SUPABASE_DB}/postgres"
    return env


def formatear_env(env: Mapping[str, str]) -> str:
    cabecera = (
        "# GENERADO por `python scripts/local.py up`. Valores de PRUEBA, solo para esta máquina. NO se versiona.\n"
        "# Si lo borrás, `up` lo vuelve a generar (con contraseñas nuevas: hacé `reset`).\n"
    )
    return cabecera + "".join(f"{k}={v}\n" for k, v in env.items())


def leer_env(texto: str) -> dict[str, str]:
    datos: dict[str, str] = {}
    for linea in texto.splitlines():
        if linea.strip() and not linea.lstrip().startswith("#") and "=" in linea:
            clave, valor = linea.split("=", 1)
            datos[clave.strip()] = valor
    return datos


# --- bindings de puertos ---------------------------------------------------------------------------------------------


def puertos_publicos(salida_ps: str) -> set[int]:
    """Puertos del host publicados en todas las interfaces (0.0.0.0 o [::]) según `docker ps` (columna Ports)."""
    return {int(m.group(1)) for m in re.finditer(r"(?:0\.0\.0\.0|\[::\]):(\d+)(?:-\d+)?->", salida_ps)}


def verificar_bindings() -> None:
    """Falla si un puerto del compose quedó publicado fuera de 127.0.0.1; avisa si es uno de la CLI de Supabase."""
    salida = correr([_ejecutable("docker"), "ps", "--format", "{{.Ports}}"], capturar=True)
    publicos = puertos_publicos(salida)
    abiertos = sorted(publicos & set(PUERTOS_COMPOSE))
    if abiertos:
        raise ErrorLocal(f"Puertos publicados en todas las interfaces (deberían ser solo 127.0.0.1): {abiertos}")
    sb = sorted(publicos & set(PUERTOS_SUPABASE))
    if sb:
        print(
            f"AVISO: la CLI de Supabase publica {sb} en todas las interfaces (no permite fijar el host de binding). "
            "Usá este entorno solo en una red de confianza o con el firewall del sistema cerrado a esos puertos."
        )


# --- ejecución de comandos -------------------------------------------------------------------------------------------


def _ejecutable(nombre: str) -> str:
    ruta = shutil.which(nombre)
    if not ruta:
        raise ErrorLocal(f"No se encontró `{nombre}` en el PATH. Revisá los requisitos en docs/local.md.")
    return ruta


def correr(cmd: list[str], *, capturar: bool = False, check: bool = True, cwd: Path | None = None) -> str:
    resultado = subprocess.run(
        cmd, cwd=cwd or RAIZ, text=True, encoding="utf-8", errors="replace", capture_output=capturar
    )
    if check and resultado.returncode != 0:
        detalle = (resultado.stdout or "") + (resultado.stderr or "") if capturar else ""
        raise ErrorLocal(f"Falló: {' '.join(cmd[:4])} (código {resultado.returncode})\n{detalle[-1500:]}")
    return resultado.stdout if capturar else ""


def supabase_cli(*args: str, capturar: bool = False, check: bool = True) -> str:
    npx = _ejecutable("npx")
    return correr(
        [npx, "--yes", "supabase", *args, "--workdir", str(DIR_SUPABASE), "--yes"], capturar=capturar, check=check
    )


def compose(*args: str, capturar: bool = False, check: bool = True) -> str:
    docker = _ejecutable("docker")
    base = [docker, "compose", "-p", PROYECTO_COMPOSE, "-f", str(COMPOSE), "--env-file", str(ARCHIVO_ENV)]
    return correr([*base, *args], capturar=capturar, check=check)


def esperar_http(url: str, *, segundos: int, nombre: str) -> None:
    limite = time.monotonic() + segundos
    while time.monotonic() < limite:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(2)
    raise ErrorLocal(f"{nombre} no respondió en {segundos}s ({url}). Mirá `python scripts/local.py logs`.")


# --- comandos --------------------------------------------------------------------------------------------------------


def _verificar_requisitos() -> None:
    _ejecutable("docker")
    _ejecutable("npx")
    if subprocess.run([_ejecutable("docker"), "info"], capture_output=True).returncode != 0:
        raise ErrorLocal("Docker no está corriendo: abrí Docker Desktop, esperá a que arranque y reintentá.")


def _estado_supabase() -> dict[str, str]:
    salida = supabase_cli("status", "-o", "env", capturar=True)
    datos = parsear_status_env(salida)
    for clave in ("ANON_KEY", "SERVICE_ROLE_KEY"):
        if clave not in datos:
            raise ErrorLocal(f"`supabase status` no informó {clave}: ¿arrancó bien Supabase local?")
    return datos


def cmd_up(_args) -> int:
    _verificar_requisitos()
    print("1/6 Supabase local (la primera vez baja imágenes: puede tardar varios minutos)...")
    preparar_workdir_supabase()
    supabase_cli("start", "-x", SUPABASE_OMITIDOS)
    estado = _estado_supabase()
    previo = leer_env(ARCHIVO_ENV.read_text(encoding="utf-8")) if ARCHIVO_ENV.exists() else {}
    env = generar_env(estado, previo)
    ARCHIVO_ENV.write_text(formatear_env(env), encoding="utf-8")
    print(f"2/6 Variables locales en {ARCHIVO_ENV.name} (no se versiona).")

    print("3/6 Imagen de los servicios Python (python:3.12)...")
    compose("build", "python-base")
    print("4/6 Roles, clave HMAC y primer platform_admin...")
    compose("run", "--rm", "--no-deps", "tools", "python", "scripts/local_demo.py", "bootstrap")

    print("5/6 Servicios (backends, frontends, jobs, simulador)...")
    compose("up", "-d", "--build", "--remove-orphans")
    for nombre, puerto in (
        ("bot", P_BOT),
        ("dashboard-backend", P_DASHBOARD_BACKEND),
        ("admin-backend", P_ADMIN_BACKEND),
        ("simulador de Telegram", P_SIMULADOR),
    ):
        esperar_http(f"http://127.0.0.1:{puerto}/health", segundos=120, nombre=nombre)
    # Los frontends instalan dependencias la primera vez: Vite responde recién después de `npm ci`.
    esperar_http(f"http://127.0.0.1:{P_DASHBOARD_FRONTEND}/", segundos=600, nombre="dashboard-frontend")
    esperar_http(f"http://127.0.0.1:{P_ADMIN_FRONTEND}/", segundos=600, nombre="admin-frontend")

    verificar_bindings()
    print("6/6 Datos de demo...")
    compose("run", "--rm", "--no-deps", "tools", "python", "scripts/local_demo.py", "seed")
    _imprimir_resumen(env["DEMO_PASSWORD"])
    return 0


def _imprimir_resumen(password: str) -> None:
    print("\n=== Entorno local listo ===")
    for nombre, url in URLS.items():
        print(f"  {nombre}: {url}")
    print("\nUsuarios de demo (solo locales):")
    print(f"  Franco (administración de la plataforma): {FRANCO_EMAIL} / {password}")
    print(f"  Dueña del negocio de prueba (owner):      {OWNER_EMAIL} / {password}")
    print(f"  Recepción (staff con permisos limitados): {STAFF_EMAIL} / {password}")
    print("  Franco y la dueña piden verificación en dos pasos: la primera vez se enrola una app TOTP (docs/local.md).")
    print("\nApagar: python scripts/local.py down")


def cmd_status(_args) -> int:
    if ARCHIVO_ENV.exists():
        compose("ps")
    else:
        print("El entorno nunca se levantó (falta .env.local). Usá: python scripts/local.py up")
    return 0


def cmd_down(_args) -> int:
    if ARCHIVO_ENV.exists() and COMPOSE.exists():
        compose("down", "--remove-orphans", check=False)
    if (DIR_SUPABASE / "supabase" / "config.toml").exists():
        supabase_cli("stop", check=False)
    print("Todo apagado. Los datos se conservan; `python scripts/local.py up` los recupera.")
    return 0


def cmd_reset(args) -> int:
    if ARCHIVO_ENV.exists() and COMPOSE.exists():
        compose("down", "--remove-orphans", "--volumes", check=False)
    if (DIR_SUPABASE / "supabase" / "config.toml").exists():
        supabase_cli("stop", "--no-backup", check=False)
    ARCHIVO_ENV.unlink(missing_ok=True)
    shutil.rmtree(DIR_LOCAL / "simulador", ignore_errors=True)
    print("Datos locales borrados. Levantando de cero...")
    return cmd_up(args)


def cmd_purga(_args) -> int:
    compose("run", "--rm", "--no-deps", "job-purga")
    return 0


def cmd_vencer(_args) -> int:
    compose("run", "--rm", "--no-deps", "tools", "python", "scripts/local_demo.py", "vencer")
    return 0


def cmd_clave(args) -> int:
    compose("run", "--rm", "--no-deps", "tools", "python", "scripts/local_demo.py", "clave", args.email)
    return 0


def cmd_logs(args) -> int:
    compose("logs", "--tail", "100", *([args.servicio] if args.servicio else []))
    return 0


def main(argv: list[str] | None = None) -> int:
    for flujo in (sys.stdout, sys.stderr):  # Windows redirigido: que un carácter raro no tire el comando
        if hasattr(flujo, "reconfigure"):
            flujo.reconfigure(errors="replace")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="comando", required=True)
    for nombre, fn in (
        ("up", cmd_up),
        ("status", cmd_status),
        ("down", cmd_down),
        ("reset", cmd_reset),
        ("purga", cmd_purga),
        ("vencer", cmd_vencer),
    ):
        sub.add_parser(nombre).set_defaults(fn=fn)
    clave = sub.add_parser("clave", help="deja la contraseña de demo a una cuenta ya creada")
    clave.add_argument("email")
    clave.set_defaults(fn=cmd_clave)
    logs = sub.add_parser("logs")
    logs.add_argument("servicio", nargs="?")
    logs.set_defaults(fn=cmd_logs)
    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except ErrorLocal as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
