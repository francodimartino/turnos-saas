"""Configuración del admin-backend, leída del entorno y validada al arrancar (no por request).

Variables:
  ADMIN_DATABASE_URL       conexión directa como el rol `platform_admin` (nunca el dueño de las migraciones).
  ADMIN_READONLY_DATABASE_URL  conexión directa como el rol `platform_admin_readonly` (solo SELECT): pool aparte, solo
                           para la impersonación de solo lectura (§2.11.4, §3.2).
  SUPABASE_URL             URL del proyecto Supabase (https; http solo hacia loopback en desarrollo local).
  SUPABASE_ANON_KEY        anon key: la única clave de Supabase que usa el proxy de login/MFA.
  ADMIN_ORIGIN             único origen (esquema://host[:puerto]) del admin-frontend: lo usan CORS y la
                           comprobación de origen de los métodos que escriben.
  COOKIE_SECURE            "true" (defecto) o "false" (solo desarrollo sobre http).
  COOKIE_SAMESITE          "strict" (defecto) o "lax". "none" no se admite.
  COOKIE_REFRESH_MAX_AGE   segundos de vida de la cookie del refresh token (defecto 7 días).
  DB_STATEMENT_CACHE_SIZE  "0" si se conecta por el pooler de Supabase en modo transacción (ver platform_tenancy).
  SENTRY_DSN               opcional; vacío = Sentry apagado.
  LOGIN_LIMITE_IP          intentos por IP en la ventana, en /auth/login y /auth/mfa/verificar
                           (defecto 20). Entero positivo; si es inválido el servicio no arranca.
  LOGIN_LIMITE_REFRESCO    intentos por IP en la ventana en /auth/refrescar, con contador propio y tope mayor que el
                           del login (defecto 60), para que un login agotado no impida renovar la sesión.
  LOGIN_LIMITE_EMAIL       intentos FALLIDOS por email en la ventana, en /auth/login (defecto 5).
  LOGIN_VENTANA_SEG        ventana de los límites, en segundos (defecto 900).
  LOGIN_IP_HEADER          vacío (defecto) = IP del socket; "CF-Connecting-IP" o "X-Forwarded-For" para tomarla del
                           proxy delante del servicio. Sin esta variable el header se ignora (se puede falsificar).
  LOGIN_PROXIES_CONFIABLES con X-Forwarded-For, cuántos proxies propios hay delante (defecto 1).
  TURNSTILE_SECRET_KEY     secret de Cloudflare Turnstile; vacío (defecto) = captcha apagado. Con valor, el login
                           exige `turnstile_token` válido.

`SUPABASE_SERVICE_ROLE_KEY` NO se lee acá: solo la usa `platform_admin.ClienteAuthAdminHttp` (Auth Admin API,
invitaciones), construido en `main.py`, y no se guarda en `Config` para que no llegue a logs ni a reprs.
La verificación de JWT (SUPABASE_JWT_SECRET o SUPABASE_JWKS_URL, ...) la configura `platform_auth`.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})
_SAMESITE = ("strict", "lax")
_UNA_SEMANA = 7 * 24 * 3600


class ConfigInvalida(ValueError):
    """Falta una variable o su valor es inseguro/inconsistente. Se lanza al arrancar."""


@dataclass(frozen=True)
class Config:
    database_url: str = field(repr=False)
    supabase_url: str
    supabase_anon_key: str = field(repr=False)
    admin_origin: str
    readonly_database_url: str = field(default="", repr=False)
    cookie_secure: bool = True
    cookie_samesite: str = "strict"
    cookie_refresh_max_age: int = _UNA_SEMANA
    statement_cache_size: int | None = None
    sentry_dsn: str | None = field(default=None, repr=False)
    login_limite_ip: int = 20
    login_limite_email: int = 5
    login_ventana_seg: int = 900
    refresco_limite_ip: int = 60
    turnstile_secret_key: str = field(default="", repr=False)

    @classmethod
    def desde_entorno(cls, entorno: Mapping[str, str]) -> "Config":
        def obligatoria(nombre: str) -> str:
            valor = (entorno.get(nombre) or "").strip()
            if not valor:
                raise ConfigInvalida(f"Falta la variable de entorno {nombre}")
            return valor

        database_url = obligatoria("ADMIN_DATABASE_URL")
        readonly_url = obligatoria("ADMIN_READONLY_DATABASE_URL")
        supabase_url = _validar_url_supabase(obligatoria("SUPABASE_URL"))
        anon = obligatoria("SUPABASE_ANON_KEY")
        origen = _validar_origen(obligatoria("ADMIN_ORIGIN"))

        samesite = (entorno.get("COOKIE_SAMESITE") or "strict").strip().lower()
        if samesite not in _SAMESITE:
            raise ConfigInvalida("COOKIE_SAMESITE debe ser 'strict' o 'lax' ('none' no se admite)")

        _validar_ip_header(entorno)

        return cls(
            database_url=database_url,
            supabase_url=supabase_url,
            supabase_anon_key=anon,
            admin_origin=origen,
            readonly_database_url=readonly_url,
            cookie_secure=_booleano(entorno.get("COOKIE_SECURE"), "COOKIE_SECURE", True),
            cookie_samesite=samesite,
            cookie_refresh_max_age=_entero(
                entorno.get("COOKIE_REFRESH_MAX_AGE"), "COOKIE_REFRESH_MAX_AGE", _UNA_SEMANA
            ),
            statement_cache_size=_entero_o_none(entorno.get("DB_STATEMENT_CACHE_SIZE"), "DB_STATEMENT_CACHE_SIZE"),
            sentry_dsn=(entorno.get("SENTRY_DSN") or "").strip() or None,
            login_limite_ip=_entero(entorno.get("LOGIN_LIMITE_IP"), "LOGIN_LIMITE_IP", 20),
            login_limite_email=_entero(entorno.get("LOGIN_LIMITE_EMAIL"), "LOGIN_LIMITE_EMAIL", 5),
            login_ventana_seg=_entero(entorno.get("LOGIN_VENTANA_SEG"), "LOGIN_VENTANA_SEG", 900),
            refresco_limite_ip=_entero(entorno.get("LOGIN_LIMITE_REFRESCO"), "LOGIN_LIMITE_REFRESCO", 60),
            turnstile_secret_key=(entorno.get("TURNSTILE_SECRET_KEY") or "").strip(),
        )


def _validar_ip_header(entorno: Mapping[str, str]) -> None:
    """`proteccion_login.ip_real` lee estas variables del entorno del proceso; acá se validan al arrancar."""
    header = (entorno.get("LOGIN_IP_HEADER") or "").strip().lower()
    if header not in ("", "cf-connecting-ip", "x-forwarded-for"):
        raise ConfigInvalida("LOGIN_IP_HEADER debe ser CF-Connecting-IP o X-Forwarded-For (o estar vacía)")
    _entero(entorno.get("LOGIN_PROXIES_CONFIABLES"), "LOGIN_PROXIES_CONFIABLES", 1)


def _validar_url_supabase(url: str) -> str:
    partes = urlsplit(url)
    if partes.scheme != "https" and not (partes.scheme == "http" and partes.hostname in _LOOPBACK):
        raise ConfigInvalida("SUPABASE_URL debe ser https (http solo hacia loopback)")
    if not partes.hostname:
        raise ConfigInvalida("SUPABASE_URL sin host")
    return url.rstrip("/")


def _validar_origen(origen: str) -> str:
    partes = urlsplit(origen)
    if (
        partes.scheme not in ("http", "https")
        or not partes.hostname
        or partes.path not in ("", "/")
        or (partes.query or partes.fragment or partes.username)
    ):
        raise ConfigInvalida("ADMIN_ORIGIN debe ser un único origen: esquema://host[:puerto], sin ruta ni comodines")
    return f"{partes.scheme}://{partes.netloc}"


def _booleano(valor: str | None, nombre: str, defecto: bool) -> bool:
    if valor is None or not valor.strip():
        return defecto
    v = valor.strip().lower()
    if v in ("true", "1", "si", "sí"):
        return True
    if v in ("false", "0", "no"):
        return False
    raise ConfigInvalida(f"{nombre} debe ser true o false")


def _entero(valor: str | None, nombre: str, defecto: int) -> int:
    if valor is None or not valor.strip():
        return defecto
    try:
        n = int(valor)
    except ValueError:
        raise ConfigInvalida(f"{nombre} debe ser un entero") from None
    if n <= 0:
        raise ConfigInvalida(f"{nombre} debe ser positivo")
    return n


def _entero_o_none(valor: str | None, nombre: str) -> int | None:
    if valor is None or not valor.strip():
        return None
    try:
        n = int(valor)
    except ValueError:
        raise ConfigInvalida(f"{nombre} debe ser un entero") from None
    if n < 0:
        raise ConfigInvalida(f"{nombre} no puede ser negativo")
    return n
