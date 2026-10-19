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

`SUPABASE_SERVICE_ROLE_KEY` NO se lee acá: solo la usa `platform_auth.SupabaseAuthAdmin` (Auth Admin API,
invitaciones), construido en `main.py`, y no se guarda en `Config` para que no llegue a logs ni a reprs.
La verificación de JWT (SUPABASE_JWT_SECRET o SUPABASE_JWKS_URL, ...) la configura `platform_auth`.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

from proteccion_login import ConfigIp
from turnos_config import (
    ConfigInvalida,
    booleano,
    encabezado_ip,
    entero,
    entero_o_none,
    obligatoria,
    samesite,
    texto_opcional,
    validar_origen,
    validar_url_supabase,
)
from turnos_sesion import CookiesSesion

_UNA_SEMANA = 7 * 24 * 3600


__all__ = ["Config", "ConfigInvalida"]


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
    config_ip: ConfigIp = ConfigIp()

    @property
    def cookies_sesion(self) -> CookiesSesion:
        """Cookies de sesión de este backend (prefijo `adm_`), con los atributos de esta configuración."""
        return CookiesSesion(
            "adm",
            secure=self.cookie_secure,
            samesite=self.cookie_samesite,
            refresh_max_age=self.cookie_refresh_max_age,
        )

    @classmethod
    def desde_entorno(cls, entorno: Mapping[str, str]) -> "Config":
        database_url = obligatoria(entorno, "ADMIN_DATABASE_URL")
        readonly_url = obligatoria(entorno, "ADMIN_READONLY_DATABASE_URL")
        supabase_url = validar_url_supabase(obligatoria(entorno, "SUPABASE_URL"))
        anon = obligatoria(entorno, "SUPABASE_ANON_KEY")
        origen = validar_origen(obligatoria(entorno, "ADMIN_ORIGIN"), "ADMIN_ORIGIN")

        cookie_samesite = samesite(entorno)
        config_ip = _config_ip(entorno)

        return cls(
            database_url=database_url,
            supabase_url=supabase_url,
            supabase_anon_key=anon,
            admin_origin=origen,
            readonly_database_url=readonly_url,
            cookie_secure=booleano(entorno.get("COOKIE_SECURE"), "COOKIE_SECURE", True),
            cookie_samesite=cookie_samesite,
            cookie_refresh_max_age=entero(entorno.get("COOKIE_REFRESH_MAX_AGE"), "COOKIE_REFRESH_MAX_AGE", _UNA_SEMANA),
            statement_cache_size=entero_o_none(entorno.get("DB_STATEMENT_CACHE_SIZE"), "DB_STATEMENT_CACHE_SIZE"),
            sentry_dsn=texto_opcional(entorno, "SENTRY_DSN") or None,
            login_limite_ip=entero(entorno.get("LOGIN_LIMITE_IP"), "LOGIN_LIMITE_IP", 20),
            login_limite_email=entero(entorno.get("LOGIN_LIMITE_EMAIL"), "LOGIN_LIMITE_EMAIL", 5),
            login_ventana_seg=entero(entorno.get("LOGIN_VENTANA_SEG"), "LOGIN_VENTANA_SEG", 900),
            refresco_limite_ip=entero(entorno.get("LOGIN_LIMITE_REFRESCO"), "LOGIN_LIMITE_REFRESCO", 60),
            turnstile_secret_key=texto_opcional(entorno, "TURNSTILE_SECRET_KEY"),
            config_ip=config_ip,
        )


def _config_ip(entorno: Mapping[str, str]) -> ConfigIp:
    """Valida al arrancar la config de `proteccion_login.ip_real`, que no vuelve a leer el entorno."""
    header = encabezado_ip(entorno)
    proxies = entero(entorno.get("LOGIN_PROXIES_CONFIABLES"), "LOGIN_PROXIES_CONFIABLES", 1)
    return ConfigIp(header=header, proxies_confiables=proxies)
