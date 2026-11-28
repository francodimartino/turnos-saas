"""Cliente de Supabase Storage (REST) para subir, borrar y servir el logo de un negocio.

- Solo PNG, JPG o WebP, detectados por los bytes iniciales (nunca por la extensión ni el Content-Type que mande el
  cliente), hasta 1 MB. El SVG queda afuera porque puede llevar scripts.
- La ruta del objeto es `<tenant_id>/logo-<id>.<ext>`, con un id aleatorio por subida: el tenant se normaliza a UUID,
  la extensión sale del formato detectado y el id lo genera el cliente, así que ningún dato del usuario puede salir de
  la carpeta del negocio. Como cada subida tiene su propio nombre, ninguna operación pisa ni borra el objeto que otra
  acaba de guardar: el borrado es siempre de una ruta concreta (`borrar_ruta`) o de lo anterior y viejo
  (`borrar_anteriores`, que respeta una gracia para no tocar subidas en curso).
- El cliente HTTP se crea al construir el objeto (al arrancar el servicio), con timeout explícito por operación y un
  plazo total por pedido (`asyncio.wait_for`). No hay reintentos: en una subida duplicarían o dejarían estados raros.
- Los errores del proveedor se traducen a errores propios con mensaje fijo: su respuesta no se refleja ni se loguea,
  y la clave de servicio no aparece en `repr`, logs ni mensajes.
"""

import asyncio
import ipaddress
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

_log = logging.getLogger("turnos_almacenamiento.cliente")

TAMANO_MAXIMO_BYTES = 1024 * 1024
EXTENSIONES = ("png", "jpg", "webp")
_MIME = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}
# `uuid4().hex`: 32 caracteres hexadecimales en minúscula. Es el mismo patrón que valida la base en `logo_ruta`.
LARGO_ID_OBJETO = 32
GRACIA_LIMPIEZA_SEG = 300  # una subida en curso dura como mucho el plazo del cliente: nada más nuevo se limpia
_PATRON_ID = re.compile(r"[0-9a-f]{32}\Z")
_PATRON_NOMBRE = re.compile(r"logo-[0-9a-f]{32}\.(?:png|jpg|webp)\Z")


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


class ObjetoNoEncontrado(AlmacenamientoNoDisponible):
    """El proveedor respondió 404: el objeto (o el bucket) no existe. Al borrar significa que ya no está."""

    mensaje = "El objeto no existe en el almacenamiento"


class AlmacenamientoSinRespuesta(AlmacenamientoNoDisponible):
    """Se agotó el plazo o se cortó la conexión sin respuesta: la operación pudo haberse aplicado igual.

    `ruta` es el objeto de una subida que pudo quedar guardado (None en las demás operaciones).
    """

    def __init__(self, mensaje: str | None = None, ruta: str | None = None):
        super().__init__(mensaje)
        self.ruta = ruta


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


def ruta_logo(tenant_id: Any, extension: str, id_objeto: str) -> str:
    """`<tenant_id>/logo-<id>.<ext>`; falla si el tenant no es un UUID, la extensión no es de las permitidas o el id no
    son 32 hexadecimales en minúscula."""
    if extension not in EXTENSIONES:
        raise ErrorAlmacenamiento("Extensión no permitida")
    if not isinstance(id_objeto, str) or len(id_objeto) != LARGO_ID_OBJETO or not _PATRON_ID.match(id_objeto):
        raise ErrorAlmacenamiento("Identificador de objeto inválido")
    return f"{_carpeta_tenant(tenant_id)}/logo-{id_objeto}.{extension}"


def _validar_ruta(tenant_id: Any, ruta: Any) -> str:
    """La ruta tiene que ser un logo del propio tenant (`<tenant_id>/logo-<id>.<ext>`)."""
    carpeta = _carpeta_tenant(tenant_id)
    if (
        not isinstance(ruta, str)
        or not ruta.startswith(carpeta + "/")
        or not _PATRON_NOMBRE.match(ruta[len(carpeta) + 1 :])
    ):
        raise ErrorAlmacenamiento("Ruta de logo inválida")
    return ruta


def _validar_url(url: str) -> str:
    try:
        partes = urlsplit(url or "")
        host = partes.hostname
        _ = partes.port  # valida el puerto: lanza ValueError si no es un número válido
    except ValueError:
        raise ConfiguracionAlmacenamientoInvalida("La URL de almacenamiento es inválida") from None
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


def _fecha(valor: Any) -> float | None:
    if not isinstance(valor, str):
        return None
    try:
        return datetime.fromisoformat(valor.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


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
        self._plazo_total = timeout
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
            # httpx fija el plazo por operación, no el total: wait_for acota la duración completa del pedido.
            r = await asyncio.wait_for(
                self._http.request(metodo, url, headers={**self._cabeceras, **extra}, **kw), self._plazo_total
            )
        except asyncio.TimeoutError:  # noqa: UP041 - en Python 3.10 no es el TimeoutError builtin
            _log.warning("El almacenamiento superó el plazo total")
            raise AlmacenamientoSinRespuesta from None
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as e:
            _log.warning("Falla de red con el almacenamiento (%s)", type(e).__name__)
            raise AlmacenamientoSinRespuesta from None
        except (httpx.HTTPError, httpx.InvalidURL) as e:
            _log.warning("Falla de red con el almacenamiento (%s)", type(e).__name__)
            raise AlmacenamientoNoDisponible from None
        if not r.is_success:
            _log.warning("El almacenamiento respondió %s", r.status_code)
            if r.status_code == 404:
                raise ObjetoNoEncontrado
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
        """Sube el logo con un nombre nuevo y devuelve su ruta. No borra nada: lo anterior se limpia aparte.

        `content_type_declarado` y `nombre_original` se ignoran a propósito. Si no hay respuesta (plazo agotado o
        conexión cortada) levanta `AlmacenamientoSinRespuesta` con la `ruta` que pudo quedar guardada.
        """
        del content_type_declarado, nombre_original
        carpeta = _carpeta_tenant(tenant_id)
        if len(contenido) > TAMANO_MAXIMO_BYTES:
            raise ArchivoDemasiadoGrande
        ext = detectar_formato(contenido)
        if ext is None:
            raise TipoNoPermitido
        ruta = ruta_logo(carpeta, ext, uuid.uuid4().hex)
        try:
            await self._pedir(
                "POST",
                self._url("object", self._bucket, quote(ruta)),
                content=contenido,
                headers={"Content-Type": _MIME[ext]},
            )
        except AlmacenamientoSinRespuesta as e:
            e.ruta = ruta
            raise
        return ruta

    async def _borrar_rutas(self, rutas: list[str]) -> None:
        if rutas:
            await self._pedir("DELETE", self._url("object", self._bucket), json={"prefixes": rutas})

    async def borrar_ruta(self, tenant_id: Any, ruta: str) -> None:
        """Borra un objeto concreto del logo del negocio (su ruta tiene que ser de ese negocio)."""
        await self._borrar_rutas([_validar_ruta(tenant_id, ruta)])

    async def borrar_anteriores(
        self,
        tenant_id: Any,
        vigente: str | None,
        *,
        gracia_seg: float = GRACIA_LIMPIEZA_SEG,
        ahora: datetime | None = None,
    ) -> list[str]:
        """Borra los logos del negocio que no son `vigente` y tienen más de `gracia_seg` de antigüedad.

        La gracia protege a una subida en curso: su objeto ya está en Storage pero su ruta todavía no se guardó.
        Un objeto sin fecha legible se conserva. Devuelve las rutas borradas.
        """
        carpeta = _carpeta_tenant(tenant_id)
        if vigente is not None:
            _validar_ruta(carpeta, vigente)
        r = await self._pedir(
            "POST",
            self._url("object", "list", self._bucket),
            json={"prefix": carpeta, "limit": 100, "offset": 0},
        )
        try:
            objetos = r.json()
            if not isinstance(objetos, list):
                raise TypeError
        except (ValueError, TypeError):
            raise AlmacenamientoNoDisponible from None
        limite = (ahora or datetime.now(timezone.utc)).timestamp() - gracia_seg  # noqa: UP017 - Python 3.10
        a_borrar: list[str] = []
        for o in objetos:
            nombre = o.get("name") if isinstance(o, dict) else None
            if not isinstance(nombre, str) or not _PATRON_NOMBRE.match(nombre):
                continue
            ruta = f"{carpeta}/{nombre}"
            if ruta == vigente:
                continue
            creado = _fecha(o.get("created_at"))
            if creado is not None and creado <= limite:
                a_borrar.append(ruta)
        await self._borrar_rutas(a_borrar)
        return a_borrar

    async def url_publica_o_firmada(self, tenant_id: Any, ruta: str, *, expira_seg: int = 3600) -> str:
        ruta = _validar_ruta(tenant_id, ruta)
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
