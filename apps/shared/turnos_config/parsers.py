"""Parsers y validaciones de variables de entorno, con el mensaje de error de arranque de cada una."""

from collections.abc import Mapping
from urllib.parse import urlsplit

_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})
_SAMESITE = ("strict", "lax")
_HEADERS_IP = ("", "cf-connecting-ip", "x-forwarded-for")


class ConfigInvalida(ValueError):
    """Falta una variable o su valor es inseguro/inconsistente. Se lanza al arrancar."""


def obligatoria(entorno: Mapping[str, str], nombre: str) -> str:
    valor = (entorno.get(nombre) or "").strip()
    if not valor:
        raise ConfigInvalida(f"Falta la variable de entorno {nombre}")
    return valor


def texto_opcional(entorno: Mapping[str, str], nombre: str) -> str:
    return (entorno.get(nombre) or "").strip()


def booleano(valor: str | None, nombre: str, defecto: bool) -> bool:
    if valor is None or not valor.strip():
        return defecto
    v = valor.strip().lower()
    if v in ("true", "1", "si", "sí"):
        return True
    if v in ("false", "0", "no"):
        return False
    raise ConfigInvalida(f"{nombre} debe ser true o false")


def entero(valor: str | None, nombre: str, defecto: int) -> int:
    """Entero positivo; vacío o ausente = `defecto`."""
    if valor is None or not valor.strip():
        return defecto
    try:
        n = int(valor)
    except ValueError:
        raise ConfigInvalida(f"{nombre} debe ser un entero") from None
    if n <= 0:
        raise ConfigInvalida(f"{nombre} debe ser positivo")
    return n


def entero_o_none(valor: str | None, nombre: str) -> int | None:
    """Entero >= 0; vacío o ausente = None."""
    if valor is None or not valor.strip():
        return None
    try:
        n = int(valor)
    except ValueError:
        raise ConfigInvalida(f"{nombre} debe ser un entero") from None
    if n < 0:
        raise ConfigInvalida(f"{nombre} no puede ser negativo")
    return n


def validar_url_supabase(url: str) -> str:
    partes = urlsplit(url)
    if partes.scheme != "https" and not (partes.scheme == "http" and partes.hostname in _LOOPBACK):
        raise ConfigInvalida("SUPABASE_URL debe ser https (http solo hacia loopback)")
    if not partes.hostname:
        raise ConfigInvalida("SUPABASE_URL sin host")
    return url.rstrip("/")


def validar_origen(origen: str, nombre: str) -> str:
    """Un único origen `esquema://host[:puerto]`, sin ruta ni comodines; devuelve la forma normalizada."""
    partes = urlsplit(origen)
    if (
        partes.scheme not in ("http", "https")
        or not partes.hostname
        or partes.path not in ("", "/")
        or (partes.query or partes.fragment or partes.username)
    ):
        raise ConfigInvalida(f"{nombre} debe ser un único origen: esquema://host[:puerto], sin ruta ni comodines")
    return f"{partes.scheme}://{partes.netloc}"


def samesite(entorno: Mapping[str, str]) -> str:
    """`COOKIE_SAMESITE`: "strict" (defecto) o "lax"; "none" no se admite."""
    valor = (entorno.get("COOKIE_SAMESITE") or "strict").strip().lower()
    if valor not in _SAMESITE:
        raise ConfigInvalida("COOKIE_SAMESITE debe ser 'strict' o 'lax' ('none' no se admite)")
    return valor


def encabezado_ip(entorno: Mapping[str, str]) -> str:
    """`LOGIN_IP_HEADER` en minúsculas: vacío (IP del socket), cf-connecting-ip o x-forwarded-for."""
    header = (entorno.get("LOGIN_IP_HEADER") or "").strip().lower()
    if header not in _HEADERS_IP:
        raise ConfigInvalida("LOGIN_IP_HEADER debe ser CF-Connecting-IP o X-Forwarded-For (o estar vacía)")
    return header
