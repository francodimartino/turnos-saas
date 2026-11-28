"""Configuración del web-backend, leída del entorno y validada al arrancar (no por request).

Variables:
  WEB_DATABASE_URL         conexión directa como el rol `web_reservas` (nunca el dueño de las migraciones).
  WEB_ORIGIN               único origen (esquema://host[:puerto]) de la web de reservas: lo usa CORS. Sin comodines;
                           https, salvo http hacia una máquina local en desarrollo.
  SUPABASE_URL             opcional: URL del proyecto Supabase (https, sin credenciales; http solo hacia loopback).
                           No es un secreto: con ella se arma la URL pública del logo (bucket de solo lectura). Sin
                           ella, la página del negocio sale sin logo. Este servicio no lee ninguna clave de Storage.
  DB_STATEMENT_CACHE_SIZE  "0" si se conecta por el pooler de Supabase en modo transacción (ver platform_tenancy).
  HTTP_MAX_BODY_BYTES      tope del cuerpo de una petición (defecto 16384; entero >= 1).
  SENTRY_DSN               opcional; vacío = Sentry apagado.
Un valor inválido (no numérico, 0, negativo, origen con comodín o ruta) impide arrancar.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from turnos_config import (
    ConfigInvalida,
    entero,
    entero_o_none,
    obligatoria,
    texto_opcional,
    validar_origen,
    validar_url_supabase,
)

__all__ = ["Config", "ConfigInvalida"]

LIMITE_BODY_DEFECTO = 16 * 1024
_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})


def _origen_web(valor: str) -> str:
    origen = validar_origen(valor, "WEB_ORIGIN")
    partes = urlsplit(origen)
    if partes.scheme != "https" and partes.hostname not in _LOOPBACK:
        raise ConfigInvalida("WEB_ORIGIN debe ser https (http solo hacia una máquina local)")
    if "*" in origen or "," in origen:
        raise ConfigInvalida("WEB_ORIGIN debe ser un único origen, sin comodines")
    return origen


@dataclass(frozen=True)
class Config:
    database_url: str = field(repr=False)
    web_origin: str
    supabase_url: str | None = None
    statement_cache_size: int | None = None
    max_body_bytes: int = LIMITE_BODY_DEFECTO
    sentry_dsn: str | None = field(default=None, repr=False)

    @classmethod
    def desde_entorno(cls, entorno: Mapping[str, str]) -> "Config":
        database_url = obligatoria(entorno, "WEB_DATABASE_URL")
        origen = _origen_web(obligatoria(entorno, "WEB_ORIGIN"))

        url = texto_opcional(entorno, "SUPABASE_URL")
        partes = urlsplit(url)
        if url and (partes.username or partes.password or partes.query or partes.fragment):
            raise ConfigInvalida("SUPABASE_URL no puede llevar credenciales, query ni fragmento")

        return cls(
            database_url=database_url,
            web_origin=origen,
            supabase_url=validar_url_supabase(url) if url else None,
            statement_cache_size=entero_o_none(entorno.get("DB_STATEMENT_CACHE_SIZE"), "DB_STATEMENT_CACHE_SIZE"),
            max_body_bytes=entero(entorno.get("HTTP_MAX_BODY_BYTES"), "HTTP_MAX_BODY_BYTES", LIMITE_BODY_DEFECTO),
            sentry_dsn=texto_opcional(entorno, "SENTRY_DSN") or None,
        )
