"""Datos de demo del entorno local (lo corre `scripts/local.py` dentro de un contenedor; no se usa a mano).

  bootstrap   Crea la cuenta de Franco en Supabase Auth local y corre `setup_proyecto.py` (primer platform_admin, clave
              HMAC y contraseñas de los 6 roles de login). Idempotente.
  seed        Carga un negocio de prueba entero por las APIs reales de la app (alta de negocio, owner, catálogo,
              disponibilidad, staff, clientes e integración de Telegram) y registra su bot en el simulador. Si el
              negocio ya existe no hace nada (idempotente).
  clave EMAIL Deja la contraseña de demo a una cuenta que ya existe (útil para el owner de un negocio nuevo).
  vencer      Adelanta el vencimiento de los turnos pendientes para que el job de vencimiento los cancele.

Todo el contenido es de prueba y solo vive en la base local. Franco y la duena piden verificación en dos pasos (TOTP):
para poder cargar los datos por la API, el script enrola un factor temporal y lo BORRA al terminar, así cada uno
enrola su propia app la primera vez que entra.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hmac
import json
import os
import secrets
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

SLUG = "estetica-demo"
NEGOCIO = "Estética Demo"
BOT_TOKEN_PREFIJO = "900001"
ZONA_AR = timezone(timedelta(hours=-3))


def totp(secreto_b32: str, ahora: float | None = None) -> str:
    """Código TOTP (RFC 6238: SHA-1, 6 dígitos, 30 s) para el secreto base32 que informa Supabase al enrolar."""
    limpio = secreto_b32.replace(" ", "").upper()
    clave = base64.b32decode(limpio + "=" * (-len(limpio) % 8))
    contador = int((time.time() if ahora is None else ahora) // 30)
    digest = hmac.new(clave, contador.to_bytes(8, "big"), "sha1").digest()
    desplazamiento = digest[-1] & 15
    codigo = (int.from_bytes(digest[desplazamiento : desplazamiento + 4], "big") & 0x7FFFFFFF) % 1_000_000
    return f"{codigo:06d}"


class AuthLocal:
    """Auth Admin API de Supabase local (con la service_role local)."""

    def __init__(self, url: str, service_role: str) -> None:
        self._http = httpx.Client(
            base_url=url.rstrip("/") + "/auth/v1",
            headers={"apikey": service_role, "Authorization": f"Bearer {service_role}"},
            timeout=20,
        )

    def esperar(self, segundos: int = 90) -> None:
        limite = time.monotonic() + segundos
        while time.monotonic() < limite:
            try:
                if self._http.get("/health").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(2)
        raise SystemExit("Supabase Auth local no responde.")

    def buscar(self, email: str) -> dict | None:
        pagina = 1
        while True:
            r = self._http.get("/admin/users", params={"page": pagina, "per_page": 200})
            r.raise_for_status()
            usuarios = r.json().get("users", [])
            for u in usuarios:
                if (u.get("email") or "").lower() == email.lower():
                    return u
            if len(usuarios) < 200:
                return None
            pagina += 1

    def asegurar(self, email: str, password: str, nombre: str) -> str:
        """Crea la cuenta (confirmada, con nombre) o, si ya existe, le deja la contraseña de demo."""
        existente = self.buscar(email)
        cuerpo = {"password": password, "email_confirm": True, "user_metadata": {"full_name": nombre}}
        if existente is None:
            r = self._http.post("/admin/users", json={"email": email, **cuerpo})
        else:
            r = self._http.put(f"/admin/users/{existente['id']}", json=cuerpo)
        r.raise_for_status()
        return r.json()["id"]

    def poner_clave(self, email: str, password: str) -> bool:
        """Deja `password` a una cuenta que ya existe (y la confirma). False si no existe."""
        existente = self.buscar(email)
        if existente is None:
            return False
        r = self._http.put(f"/admin/users/{existente['id']}", json={"password": password, "email_confirm": True})
        r.raise_for_status()
        return True

    def _factores(self, usuario_id: str) -> list[dict]:
        # El listado de usuarios no trae los factores: hay que pedir la ficha de la cuenta.
        r = self._http.get(f"/admin/users/{usuario_id}")
        r.raise_for_status()
        return r.json().get("factors") or []

    def borrar_factores(self, email: str) -> None:
        """Borra los factores MFA de la cuenta y comprueba que no quedó ninguno (sería una cuenta inaccesible)."""
        usuario = self.buscar(email)
        if usuario is None:
            return
        for f in self._factores(usuario["id"]):
            self._http.delete(f"/admin/users/{usuario['id']}/factors/{f['id']}").raise_for_status()
        if self._factores(usuario["id"]):
            raise SystemExit(f"No se pudo borrar el factor MFA temporal de {email}: hacé `reset`.")


class Sesion:
    """Cliente de uno de los backends con la cookie de sesión del login real (como el navegador)."""

    def __init__(self, url: str, origen: str) -> None:
        self.http = httpx.Client(base_url=url, headers={"Origin": origen}, timeout=60)

    def entrar(self, email: str, password: str) -> None:
        r = self.http.post("/auth/login", json={"email": email, "password": password})
        r.raise_for_status()
        paso = r.json()["siguiente_paso"]
        if paso == "listo":
            return
        if paso != "enrolar_mfa":
            raise SystemExit(f"{email} ya tiene verificación en dos pasos: hacé `reset` o borrá su factor en Studio.")
        enrolado = self.http.post("/auth/mfa/enrolar")
        enrolado.raise_for_status()
        factor, secreto = enrolado.json()["factor_id"], enrolado.json()["secret"]
        for _ in range(3):
            r = self.http.post("/auth/mfa/verificar", json={"factor_id": factor, "codigo": totp(secreto)})
            if r.status_code == 200:
                return
            time.sleep(10)  # puede haber cruzado el límite de los 30 s del código
        r.raise_for_status()

    def pedir(self, metodo: str, ruta: str, **kw) -> dict | list | None:
        r = self.http.request(metodo, ruta, **kw)
        if r.status_code >= 400:
            raise SystemExit(f"{metodo} {ruta} -> {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else None


async def _bootstrap(env: dict[str, str]) -> int:
    import setup_proyecto

    auth = AuthLocal(env["SUPABASE_URL"], env["SUPABASE_SERVICE_ROLE_KEY"])
    auth.esperar()
    auth.asegurar(env["DEMO_FRANCO_EMAIL"], env["DEMO_PASSWORD"], "Franco (demo)")
    print(f"- cuenta de Franco en Auth local: {env['DEMO_FRANCO_EMAIL']}")
    return await setup_proyecto.ejecutar(["--email", env["DEMO_FRANCO_EMAIL"]], env)


async def _tenant_existe(env: dict[str, str]) -> bool:
    import asyncpg

    conn = await asyncpg.connect(env["DB_OWNER_URL"])
    try:
        return bool(await conn.fetchval("select 1 from public.tenants where slug = $1", SLUG))
    finally:
        await conn.close()


def _sembrar(env: dict[str, str]) -> None:
    auth = AuthLocal(env["SUPABASE_URL"], env["SUPABASE_SERVICE_ROLE_KEY"])
    auth.esperar()
    password = env["DEMO_PASSWORD"]
    franco, owner_email, staff_email = env["DEMO_FRANCO_EMAIL"], env["DEMO_OWNER_EMAIL"], env["DEMO_STAFF_EMAIL"]

    # Franco y la duena van a enrolar su propia app: el factor temporal se borra siempre, también si algo falla.
    auth.borrar_factores(franco)
    try:
        admin = Sesion(env["ADMIN_BACKEND_URL"], env["ADMIN_ORIGIN"])
        admin.entrar(franco, password)

        alta = admin.pedir(
            "POST",
            "/tenants",
            json={"name": NEGOCIO, "slug": SLUG, "owner_email": owner_email, "owner_nombre": "Dueña Demo"},
        )
        tenant_id = alta["tenant_id"]
        print(f"- negocio '{NEGOCIO}' creado ({tenant_id}); invitación al owner: {alta['estado_invitacion']}")
        auth.asegurar(owner_email, password, "Dueña Demo")  # la invitación crea la cuenta: se le deja la clave de demo

        # Integración de Telegram: token y secret de PRUEBA (el simulador no los valida contra Telegram).
        token = f"{BOT_TOKEN_PREFIJO}:{secrets.token_urlsafe(24)}"
        webhook_secret = secrets.token_urlsafe(32)  # la API exige de 32 a 256 caracteres
        integracion = admin.pedir(
            "PUT",
            f"/tenants/{tenant_id}/integraciones/telegram_bot",
            json={"config": {}, "secret": json.dumps({"bot_token": token, "webhook_secret": webhook_secret})},
        )
        webhook_key = integracion["webhook_key"]
        httpx.post(
            env["SIM_URL"] + "/api/bots",
            json={"nombre": NEGOCIO, "token": token, "webhook_key": webhook_key, "webhook_secret": webhook_secret},
            timeout=15,
        ).raise_for_status()
        print("- integración de Telegram cargada y bot registrado en el simulador")
    finally:
        auth.borrar_factores(franco)

    auth.borrar_factores(owner_email)
    try:
        duena = Sesion(env["DASHBOARD_BACKEND_URL"], env["DASHBOARD_ORIGIN"])
        duena.entrar(owner_email, password)
        _catalogo(duena)
        _staff(duena, auth, staff_email, password)
    finally:
        auth.borrar_factores(owner_email)


def _catalogo(d: Sesion) -> None:
    d.pedir(
        "PATCH",
        "/local/settings",
        json={
            "settings": {
                "local_direccion": "Av. Siempre Viva 742, Springfield",
                "local_horarios": ["Lunes a sábado de 10 a 20"],
                "local_contacto": "Tel. 011 5555-0000 (local de demostración)",
            }
        },
    )
    corte = d.pedir("POST", "/servicios", json={"name": "Corte de pelo", "duration_minutes": 45, "price": 8000})
    color = d.pedir("POST", "/servicios", json={"name": "Coloración", "duration_minutes": 120, "price": 25000})
    mani = d.pedir("POST", "/servicios", json={"name": "Manicura", "duration_minutes": 40, "price": 6000})
    lucia = d.pedir("POST", "/profesionales", json={"name": "Lucía Peluquera"})
    mariano = d.pedir("POST", "/profesionales", json={"name": "Mariano Colorista"})
    # La coloración queda en modo `approval`: el turno nace pendiente hasta que el local lo aprueba.
    vinculos = [
        (lucia, corte, "auto"),
        (lucia, color, "approval"),
        (lucia, mani, "auto"),
        (mariano, corte, "auto"),
        (mariano, color, "approval"),
    ]
    for pro, servicio, modo in vinculos:
        d.pedir("PUT", f"/profesionales/{pro['id']}/servicios/{servicio['id']}", json={"confirmation_mode": modo})
    # Reglas semanales (0 = domingo): lunes a sábado.
    for dia in range(1, 7):
        d.pedir(
            "POST",
            "/reglas-disponibilidad",
            json={"professional_id": lucia["id"], "day_of_week": dia, "start_time": "10:00", "end_time": "18:00"},
        )
        d.pedir(
            "POST",
            "/reglas-disponibilidad",
            json={"professional_id": mariano["id"], "day_of_week": dia, "start_time": "12:00", "end_time": "20:00"},
        )
    manana = (datetime.now(ZONA_AR) + timedelta(days=1)).replace(hour=14, minute=0, second=0, microsecond=0)
    d.pedir(
        "POST",
        "/bloqueos-disponibilidad",
        json={
            "professional_id": lucia["id"],
            "start_at": manana.isoformat(),
            "end_at": (manana + timedelta(hours=2)).isoformat(),
            "reason": "Capacitación (bloqueo de demo)",
        },
    )
    for telefono, nombre in (
        ("+5491155550401", "Ana Gómez"),
        ("+5491155550402", "Bruno Díaz"),
        ("+5491155550403", "Carolina Ruiz"),
    ):
        d.pedir("POST", "/clientes", json={"phone": telefono, "name": nombre})
    print("- catálogo (3 servicios, 2 profesionales), disponibilidad, bloqueo y 3 clientes cargados")


def _staff(d: Sesion, auth: AuthLocal, email: str, password: str) -> None:
    # Permisos limitados: ve y aprueba turnos, pero no toca el catálogo, la agenda, los precios ni el equipo.
    d.pedir(
        "POST",
        "/staff/invitaciones",
        json={"email": email, "nombre": "Recepción Demo", "permission_keys": ["view_bookings", "approve_bookings"]},
    )
    auth.asegurar(email, password, "Recepción Demo")
    print(f"- staff con permisos limitados invitado: {email} (view_bookings, approve_bookings)")


async def _vencer(env: dict[str, str]) -> int:
    import asyncpg

    conn = await asyncpg.connect(env["DB_OWNER_URL"])
    try:
        n = await conn.fetchval(
            "with t as (update public.bookings set pending_expires_at = now() - interval '1 minute' "
            "where status = 'pending' and pending_expires_at is not null returning 1) select count(*) from t"
        )
    finally:
        await conn.close()
    print(f"{n} turno(s) pendiente(s) quedan vencidos: el job de vencimiento los cancela en su próxima vuelta.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("accion", choices=["bootstrap", "seed", "vencer", "clave"])
    p.add_argument("email", nargs="?", help="solo para `clave`")
    args = p.parse_args(argv)
    env = dict(os.environ)
    if args.accion == "bootstrap":
        return asyncio.run(_bootstrap(env))
    if args.accion == "vencer":
        return asyncio.run(_vencer(env))
    if args.accion == "clave":
        if not args.email:
            p.error("`clave` necesita el email de la cuenta")
        auth = AuthLocal(env["SUPABASE_URL"], env["SUPABASE_SERVICE_ROLE_KEY"])
        if not auth.poner_clave(args.email.strip(), env["DEMO_PASSWORD"]):
            print(f"No existe la cuenta {args.email}: creá primero el negocio o la invitación.")
            return 1
        print(f"Listo: {args.email} entra con la contraseña de demo ({env['DEMO_PASSWORD']}).")
        return 0
    if asyncio.run(_tenant_existe(env)):
        print(
            f"El negocio de demo '{NEGOCIO}' ya existe: no se carga nada de nuevo (usá `reset` para empezar de cero)."
        )
        return 0
    _sembrar(env)
    return 0


if __name__ == "__main__":
    sys.exit(main())
