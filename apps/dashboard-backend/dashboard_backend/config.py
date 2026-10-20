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
from turnos_proteccion_login import ConfigIp, LimitesLogin
from turnos_proteccion_login.limitador import LIMITE_EMAIL_DEFECTO, LIMITE_IP_DEFECTO, VENTANA_SEG_DEFECTO
from turnos_sesion import CookiesSesion

_UNA_SEMANA = 7 * 24 * 3600


__all__ = ["Config", "ConfigInvalida"]


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

    @property
    def cookies_sesion(self) -> CookiesSesion:
        """Cookies de sesión de este backend (prefijo `dash_`), con los atributos de esta configuración."""
        return CookiesSesion(
            "dash",
            secure=self.cookie_secure,
            samesite=self.cookie_samesite,
            refresh_max_age=self.cookie_refresh_max_age,
        )

    @classmethod
    def desde_entorno(cls, entorno: Mapping[str, str]) -> "Config":
        database_url = obligatoria(entorno, "DASHBOARD_DATABASE_URL")
        supabase_url = validar_url_supabase(obligatoria(entorno, "SUPABASE_URL"))
        anon = obligatoria(entorno, "SUPABASE_ANON_KEY")
        origen = validar_origen(obligatoria(entorno, "DASHBOARD_ORIGIN"), "DASHBOARD_ORIGIN")

        cookie_samesite = samesite(entorno)
        header_ip = encabezado_ip(entorno)
        proxies = entero(entorno.get("LOGIN_PROXIES_CONFIABLES"), "LOGIN_PROXIES_CONFIABLES", 1)
        limites = LimitesLogin(
            por_ip=entero(entorno.get("LOGIN_LIMITE_IP"), "LOGIN_LIMITE_IP", LIMITE_IP_DEFECTO),
            por_email=entero(entorno.get("LOGIN_LIMITE_EMAIL"), "LOGIN_LIMITE_EMAIL", LIMITE_EMAIL_DEFECTO),
            ventana_seg=entero(entorno.get("LOGIN_VENTANA_SEG"), "LOGIN_VENTANA_SEG", VENTANA_SEG_DEFECTO),
        )

        return cls(
            database_url=database_url,
            supabase_url=supabase_url,
            supabase_anon_key=anon,
            dashboard_origin=origen,
            cookie_secure=booleano(entorno.get("COOKIE_SECURE"), "COOKIE_SECURE", True),
            cookie_samesite=cookie_samesite,
            cookie_refresh_max_age=entero(entorno.get("COOKIE_REFRESH_MAX_AGE"), "COOKIE_REFRESH_MAX_AGE", _UNA_SEMANA),
            statement_cache_size=entero_o_none(entorno.get("DB_STATEMENT_CACHE_SIZE"), "DB_STATEMENT_CACHE_SIZE"),
            sentry_dsn=texto_opcional(entorno, "SENTRY_DSN") or None,
            turnstile_secret=texto_opcional(entorno, "TURNSTILE_SECRET_KEY"),
            login_limites=limites,
            config_ip=ConfigIp(header=header_ip, proxies_confiables=proxies),
            refresco_limite_ip=entero(entorno.get("LOGIN_LIMITE_REFRESCO"), "LOGIN_LIMITE_REFRESCO", 60),
        )
