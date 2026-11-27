"""Cliente de Supabase Storage (REST) para subir, borrar y servir el logo de un negocio.

- Solo PNG, JPG o WebP, detectados por los bytes iniciales (nunca por la extensión ni el Content-Type que mande el
  cliente), hasta 1 MB. El SVG queda afuera porque puede llevar scripts.
- La ruta del objeto es `<tenant_id>/logo.<ext>`: el tenant se normaliza a UUID y la extensión sale del formato
  detectado, así que ningún dato del usuario puede salir de la carpeta del negocio.
- El cliente HTTP se crea al construir el objeto (al arrancar el servicio), con timeout explícito.
- Los errores del proveedor se traducen a errores propios con mensaje fijo: su respuesta no se refleja ni se loguea,
  y la clave de servicio no aparece en `repr`, logs ni mensajes.
"""

import ipaddress
import logging
import uuid
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

_log = logging.getLogger("turnos_almacenamiento.cliente")

TAMANO_MAXIMO_BYTES = 1024 * 1024
EXTENSIONES = ("png", "jpg", "webp")
_MIME = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}


class ErrorAlmacenamiento(Exception):
    """Base de los errores del paquete: el mensaje es fijo y apto para mostrar."""

    mensaje = "No se pudo completar la operación con el almacenamiento"

    def __init__(self, mensaje: str | None = None):
        super().__init__(mensaje or self.mensaje)


class TipoNoPermitido(ErrorAlmacenamiento):
    mensaje = "El archivo debe ser una imagen PNG, JPG o WebP"


class ArchivoDemasiadoGrande(ErrorAlmacenamiento):
    mensaje = "El archivo supera el máximo de 1 MB"


class AlmacenamientoNoDisponible(ErrorAlmacenamiento):
    mensaje = "El servicio de almacenamiento no está disponible"


class ConfiguracionAlmacenamientoInvalida(ErrorAlmacenamiento):
    mensaje = "La configuración del almacenamiento es inválida"


def detectar_formato(contenido: bytes) -> str | None:
    """Devuelve `png`, `jpg` o `webp` según la firma de los primeros bytes, o None."""
    if contenido.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if contenido.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if len(contenido) >= 12 and contenido[:4] == b"RIFF" and contenido[8:12] == b"WEBP":
        return "webp"
    return None


def _carpeta_tenant(tenant_id: Any) -> str:
    if isinstance(tenant_id, uuid.UUID):
        return str(tenant_id)
    if isinstance(tenant_id, str):
        try:
            return str(uuid.UUID(tenant_id))
        except ValueError:
            pass
    raise ErrorAlmacenamiento("Identificador de negocio inválido")


def ruta_logo(tenant_id: Any, extension: str) -> str:
    """`<tenant_id>/logo.<ext>`; falla si el tenant no es un UUID o la extensión no es de las permitidas."""
    if extension not in EXTENSIONES:
        raise ErrorAlmacenamiento("Extensión no permitida")
    return f"{_carpeta_tenant(tenant_id)}/logo.{extension}"


def _validar_url(url: str) -> str:
    partes = urlsplit(url or "")
    host = partes.hostname
    if not host or partes.scheme not in ("http", "https"):
        raise ConfiguracionAlmacenamientoInvalida("La URL de almacenamiento es inválida")
    if partes.scheme == "http":
        try:
            es_loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            es_loopback = host == "localhost"
        if not es_loopback:
            raise ConfiguracionAlmacenamientoInvalida("La URL de almacenamiento debe ser https")
    return url.rstrip("/")


class ClienteAlmacenamiento:
    def __init__(
        self,
        url_base: str,
        clave_servicio: str,
        *,
        bucket: str = "logos",
        publico: bool = False,
        timeout: float = 10.0,
        http: httpx.AsyncClient | None = None,
    ):
        self._base = _validar_url(url_base)
        if not clave_servicio or not clave_servicio.strip():
            raise ConfiguracionAlmacenamientoInvalida("Falta la clave de servicio de almacenamiento")
        if not bucket or not all(c.isalnum() or c in "-_" for c in bucket):
            raise ConfiguracionAlmacenamientoInvalida("El nombre del bucket es inválido")
        if timeout <= 0:
            raise ConfiguracionAlmacenamientoInvalida("El timeout debe ser positivo")
        self._bucket = bucket
        self._publico = publico
        self._cabeceras = {"Authorization": f"Bearer {clave_servicio}", "apikey": clave_servicio}
        self._http = http or httpx.AsyncClient(timeout=timeout)

    def __repr__(self) -> str:
        return f"ClienteAlmacenamiento(bucket={self._bucket!r})"

    async def cerrar(self) -> None:
        await self._http.aclose()

    def _url(self, *partes: str) -> str:
        return "/".join([self._base, "storage", "v1", *partes])

    async def _pedir(self, metodo: str, url: str, **kw: Any) -> httpx.Response:
        extra = kw.pop("headers", {})
        try:
            r = await self._http.request(metodo, url, headers={**self._cabeceras, **extra}, **kw)
        except httpx.HTTPError as e:
            _log.warning("Falla de red con el almacenamiento (%s)", type(e).__name__)
            raise AlmacenamientoNoDisponible from None
        if r.status_code >= 400:
            _log.warning("El almacenamiento respondió %s", r.status_code)
            raise AlmacenamientoNoDisponible
        return r

    async def subir(
        self,
        tenant_id: Any,
        contenido: bytes,
        *,
        content_type_declarado: str | None = None,
        nombre_original: str | None = None,
    ) -> str:
        """Sube el logo y devuelve su ruta. `content_type_declarado` y `nombre_original` se ignoran a propósito."""
        del content_type_declarado, nombre_original
        carpeta = _carpeta_tenant(tenant_id)
        if len(contenido) > TAMANO_MAXIMO_BYTES:
            raise ArchivoDemasiadoGrande
        ext = detectar_formato(contenido)
        if ext is None:
            raise TipoNoPermitido
        ruta = ruta_logo(carpeta, ext)
        await self._pedir(
            "POST",
            self._url("object", self._bucket, quote(ruta)),
            content=contenido,
            headers={"Content-Type": _MIME[ext], "x-upsert": "true"},
        )
        await self._borrar_rutas([ruta_logo(carpeta, e) for e in EXTENSIONES if e != ext])
        return ruta

    async def _borrar_rutas(self, rutas: list[str]) -> None:
        await self._pedir("DELETE", self._url("object", self._bucket), json={"prefixes": rutas})

    async def borrar(self, tenant_id: Any) -> None:
        """Borra el logo del negocio, sea cual sea su formato."""
        carpeta = _carpeta_tenant(tenant_id)
        await self._borrar_rutas([ruta_logo(carpeta, e) for e in EXTENSIONES])

    async def url_publica_o_firmada(self, tenant_id: Any, extension: str, *, expira_seg: int = 3600) -> str:
        ruta = ruta_logo(tenant_id, extension)
        if self._publico:
            return self._url("object", "public", self._bucket, quote(ruta))
        r = await self._pedir(
            "POST", self._url("object", "sign", self._bucket, quote(ruta)), json={"expiresIn": expira_seg}
        )
        try:
            firmada = r.json()["signedURL"]
        except (ValueError, KeyError, TypeError):
            raise AlmacenamientoNoDisponible from None
        if not isinstance(firmada, str) or not firmada.startswith("/"):
            raise AlmacenamientoNoDisponible
        return self._url() + firmada
