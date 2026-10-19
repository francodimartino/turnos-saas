"""Configuración del dashboard-backend, leída del entorno y validada al arrancar (no por request).

Variables:
  DASHBOARD_DATABASE_URL   conexión directa como el rol `dashboard_service` (nunca el dueño de las migraciones).
  SUPABASE_URL             URL del proyecto Supabase (https; http solo hacia loopback en desarrollo local).
  SUPABASE_ANON_KEY        anon key, la única clave de Supabase que usa este servicio (login/MFA/invitación).
                           La service_role NO se lee acá: vive solo en el flujo de invitaciones de platform_auth.
  DASHBOARD_ORIGIN         único origen (esquema://host[:puerto]) del dashboard-frontend: lo usa CORS y la
                           comprobación de origen de los métodos que escriben.
  COOKIE_SECURE            "true" (defecto) o "false" (solo desarrollo sobre http).
  COOKIE_SAMESITE          "strict" (defecto) o "lax". "none" no se admite.
  COOKIE_REFRESH_MAX_AGE   segundos de vida de la cookie del refresh token (defecto 7 días).
  DB_STATEMENT_CACHE_SIZE  "0" si se conecta por el pooler de Supabase en modo transacción (ver platform_tenancy).
  SENTRY_DSN               opcional; vacío = Sentry apagado.
  TURNSTILE_SECRET_KEY     opcional; con valor, POST /auth/login y /auth/invitacion/aceptar exigen un
                           `turnstile_token` válido de Cloudflare Turnstile; vacío = captcha apagado (el campo es
                           opcional y se ignora).
  LOGIN_IP_HEADER          opcional; "CF-Connecting-IP" o "X-Forwarded-For": header del que se toma la IP real del
                           cliente (sin esto se usa la IP del socket y los headers se ignoran). Otro valor = error.
  LOGIN_PROXIES_CONFIABLES con X-Forwarded-For, cuántos proxies propios hay delante de la app (defecto 1; entero >= 1).
  LOGIN_LIMITE_IP          intentos por IP en la ventana en /auth/login, /auth/mfa/verificar y
                           /auth/invitacion/aceptar (defecto 20; entero >= 1).
  LOGIN_LIMITE_REFRESCO    intentos por IP en la ventana en /auth/refrescar, con contador propio y tope mayor que el
                           del login (defecto 60; entero >= 1): un login agotado no impide renovar la sesión.
  LOGIN_LIMITE_EMAIL       intentos FALLIDOS por email en la ventana en /auth/login (defecto 5; entero >= 1).
  LOGIN_VENTANA_SEG        ventana de ambos límites en segundos (defecto 900; entero >= 1).
Un valor inválido (no numérico, 0, negativo, header no soportado) impide arrancar.
La verificación de JWT (SUPABASE_JWT_SECRET o SUPABASE_JWKS_URL, ...) la configura `platform_auth`.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from proteccion_login import ConfigIp, LimitesLogin
from proteccion_login.limitador import LIMITE_EMAIL_DEFECTO, LIMITE_IP_DEFECTO, VENTANA_SEG_DEFECTO

_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})
_SAMESITE = ("strict", "lax")
_UNA_SEMANA = 7 * 24 * 3600
_HEADERS_IP = ("cf-connecting-ip", "x-forwarded-for")


class ConfigInvalida(ValueError):
    """Falta una variable o su valor es inseguro/inconsistente. Se lanza al arrancar."""


@dataclass(frozen=True)
class Config:
    database_url: str = field(repr=False)
    supabase_url: str
    supabase_anon_key: str = field(repr=False)
    dashboard_origin: str
    cookie_secure: bool = True
    cookie_samesite: str = "strict"
    cookie_refresh_max_age: int = _UNA_SEMANA
    statement_cache_size: int | None = None
    sentry_dsn: str | None = field(default=None, repr=False)
    turnstile_secret: str = field(default="", repr=False)
    refresco_limite_ip: int = 60
    login_limites: LimitesLogin = LimitesLogin(
        por_ip=LIMITE_IP_DEFECTO, por_email=LIMITE_EMAIL_DEFECTO, ventana_seg=VENTANA_SEG_DEFECTO
    )
    config_ip: ConfigIp = ConfigIp()

    @classmethod
    def desde_entorno(cls, entorno: Mapping[str, str]) -> "Config":
        def obligatoria(nombre: str) -> str:
            valor = (entorno.get(nombre) or "").strip()
            if not valor:
                raise ConfigInvalida(f"Falta la variable de entorno {nombre}")
            return valor

        database_url = obligatoria("DASHBOARD_DATABASE_URL")
        supabase_url = _validar_url_supabase(obligatoria("SUPABASE_URL"))
        anon = obligatoria("SUPABASE_ANON_KEY")
        origen = _validar_origen(obligatoria("DASHBOARD_ORIGIN"))

        samesite = (entorno.get("COOKIE_SAMESITE") or "strict").strip().lower()
        if samesite not in _SAMESITE:
            raise ConfigInvalida("COOKIE_SAMESITE debe ser 'strict' o 'lax' ('none' no se admite)")

        header_ip = (entorno.get("LOGIN_IP_HEADER") or "").strip().lower()
        if header_ip and header_ip not in _HEADERS_IP:
            raise ConfigInvalida("LOGIN_IP_HEADER debe ser CF-Connecting-IP o X-Forwarded-For")
        proxies = _entero(entorno.get("LOGIN_PROXIES_CONFIABLES"), "LOGIN_PROXIES_CONFIABLES", 1)
        limites = LimitesLogin(
            por_ip=_entero(entorno.get("LOGIN_LIMITE_IP"), "LOGIN_LIMITE_IP", LIMITE_IP_DEFECTO),
            por_email=_entero(entorno.get("LOGIN_LIMITE_EMAIL"), "LOGIN_LIMITE_EMAIL", LIMITE_EMAIL_DEFECTO),
            ventana_seg=_entero(entorno.get("LOGIN_VENTANA_SEG"), "LOGIN_VENTANA_SEG", VENTANA_SEG_DEFECTO),
        )

        return cls(
            database_url=database_url,
            supabase_url=supabase_url,
            supabase_anon_key=anon,
            dashboard_origin=origen,
            cookie_secure=_booleano(entorno.get("COOKIE_SECURE"), "COOKIE_SECURE", True),
            cookie_samesite=samesite,
            cookie_refresh_max_age=_entero(
                entorno.get("COOKIE_REFRESH_MAX_AGE"), "COOKIE_REFRESH_MAX_AGE", _UNA_SEMANA
            ),
            statement_cache_size=_entero_o_none(entorno.get("DB_STATEMENT_CACHE_SIZE"), "DB_STATEMENT_CACHE_SIZE"),
            sentry_dsn=(entorno.get("SENTRY_DSN") or "").strip() or None,
            turnstile_secret=(entorno.get("TURNSTILE_SECRET_KEY") or "").strip(),
            login_limites=limites,
            config_ip=ConfigIp(header=header_ip, proxies_confiables=proxies),
            refresco_limite_ip=_entero(entorno.get("LOGIN_LIMITE_REFRESCO"), "LOGIN_LIMITE_REFRESCO", 60),
        )


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
        raise ConfigInvalida(
            "DASHBOARD_ORIGIN debe ser un único origen: esquema://host[:puerto], sin ruta ni comodines"
        )
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
