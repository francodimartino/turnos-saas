"""Dominio de la marca del local (ARCHITECTURE.md §2.12): logo, colores y plazo de cancelación. Sin FastAPI.

El logo vive en Supabase Storage y su ruta en `tenants.settings.logo_ruta`. Storage nunca se toca dentro de una
transacción, y `logo_ruta` nunca apunta a algo que no existe:

  - Subir: (1) se sube el objeto nuevo SIN borrar los de otros formatos; (2) una transacción con
    `pg_advisory_xact_lock` por tenant guarda `logo_ruta`; (3) tras el COMMIT se borran los otros formatos. Si la base
    falla en (2), el logo anterior sigue intacto y la ruta apunta a algo que existe; el objeto nuevo queda huérfano y
    se registra en el log.
  - Borrar: (1) una transacción con el mismo lock quita `logo_ruta`; (2) tras el COMMIT se borra el objeto. Si Storage
    falla, queda un objeto huérfano (se registra), nunca una ruta colgada.
  - Operaciones cruzadas del mismo negocio (dos subidas, una subida y un borrado): cada operación toma un número de
    secuencia en este proceso; antes de guardar o de limpiar se verifica que sigue siendo la última, y si otra
    empezó después se abandona (409 `logo_reemplazado`) sin tocar la ruta. La secuencia es por proceso: con varias
    instancias del servicio solo el lock de la base serializa el guardado, y un objeto huérfano es el peor caso.

Cada transacción corta vuelve a verificar la sesión viva y el permiso, y escribe con su auditoría (`settings_updated`,
solo con los nombres de las claves tocadas). Los errores de Storage se propagan como `ErrorAlmacenamiento` (mensaje
fijo, sin la respuesta del proveedor ni la clave de servicio); la ruta los traduce a HTTP.
"""

import itertools
import logging
from collections.abc import Callable
from typing import Any
from uuid import UUID

from turnos_almacenamiento import (
    AlmacenamientoNoDisponible,
    ClienteAlmacenamiento,
    ErrorAlmacenamiento,
    detectar_formato,
    ruta_logo,
)
from turnos_dominio.settings_tenant import (
    CLAVE_LOGO,
    CLAVE_PLAZO_CANCELACION,
    CLAVES_COLOR,
    validar_valor_de_setting,
)

from dashboard_backend import settings_local

_log = logging.getLogger("dashboard_backend.marca_local")

CLAVES_APARIENCIA = (*CLAVES_COLOR, CLAVE_PLAZO_CANCELACION)

__all__ = [
    "CLAVES_APARIENCIA",
    "ErrorAlmacenamientoNoConfigurado",
    "ErrorApariencia",
    "ErrorLogoReemplazado",
    "borrar_logo",
    "modificar_apariencia",
    "subir_logo",
]


class ErrorAlmacenamientoNoConfigurado(ErrorAlmacenamiento):
    """La app se armó sin cliente de Storage."""

    mensaje = "El servicio de almacenamiento no está disponible"


class ErrorApariencia(Exception):
    """Un valor de apariencia no cumple su regla; el mensaje nombra la clave, nunca el valor."""


class ErrorLogoReemplazado(Exception):
    """Otra operación sobre el logo del mismo negocio empezó después: esta se abandona sin tocar la ruta."""


SQL_BLOQUEO_LOGO = "select pg_advisory_xact_lock(hashtextextended($1, 0))"
_secuencia = itertools.count(1)
_ultima_operacion: dict[UUID, int] = {}


def _nueva_operacion(tenant_id: UUID) -> int:
    numero = next(_secuencia)
    _ultima_operacion[tenant_id] = numero
    return numero


def _es_la_ultima(tenant_id: UUID, numero: int) -> bool:
    return _ultima_operacion.get(tenant_id) == numero


async def _guardar_ruta(conn: Any, tenant_id: UUID, valor: str | None) -> dict[str, Any]:
    """Guarda (o quita, con None) `logo_ruta` bajo el lock del negocio: serializa las operaciones sobre el logo."""
    await conn.execute(SQL_BLOQUEO_LOGO, f"logo:{tenant_id}")
    return await settings_local.modificar_settings(conn, tenant_id, {CLAVE_LOGO: valor})


# `abrir_tx()` devuelve un administrador de contexto con la transacción de la sesión, ya con el permiso verificado.
AbrirTx = Callable[[], Any]


def _requerir(almacenamiento: ClienteAlmacenamiento | None) -> ClienteAlmacenamiento:
    if almacenamiento is None:
        raise ErrorAlmacenamientoNoConfigurado
    return almacenamiento


async def subir_logo(
    abrir_tx: AbrirTx, almacenamiento: ClienteAlmacenamiento | None, contenido: bytes
) -> dict[str, Any]:
    """Sube el logo (tipo y tamaño los valida el cliente por los bytes) y guarda su ruta. Devuelve los settings."""
    cliente = _requerir(almacenamiento)
    async with abrir_tx() as tx:  # permiso y tenant de la sesión viva; la transacción se cierra antes de Storage
        tenant_id = tx.sesion.tenant_id
        formato = detectar_formato(contenido)
        if formato is not None:
            # Si la mezcla no entra en el tope de settings, se rechaza antes de subir nada.
            actuales = await settings_local.leer_settings(tx.conn, tenant_id)
            settings_local.mezclar(actuales, {CLAVE_LOGO: ruta_logo(tenant_id, formato)})
    numero = _nueva_operacion(tenant_id)
    ruta = await cliente.subir(tenant_id, contenido, limpiar_anteriores=False)
    try:
        async with abrir_tx() as tx:
            if not _es_la_ultima(tenant_id, numero):
                raise ErrorLogoReemplazado
            settings = await _guardar_ruta(tx.conn, tenant_id, ruta)
    except Exception:
        _log.warning(
            "El logo nuevo quedó en Storage sin ruta guardada",
            extra={"motivo": "logo_huerfano", "tenant_id": str(tenant_id)},
        )
        raise
    if _es_la_ultima(tenant_id, numero):
        try:
            await cliente.borrar_otros(tenant_id, ruta)
        except AlmacenamientoNoDisponible:
            _log.warning(
                "No se pudieron borrar los logos de otros formatos",
                extra={"motivo": "logo_huerfano", "tenant_id": str(tenant_id)},
            )
    return settings


async def borrar_logo(abrir_tx: AbrirTx, almacenamiento: ClienteAlmacenamiento | None) -> dict[str, Any]:
    """Quita `logo_ruta` y, tras el COMMIT, borra el objeto (cualquier formato). Es idempotente."""
    cliente = _requerir(almacenamiento)
    async with abrir_tx() as tx:
        tenant_id = tx.sesion.tenant_id
    numero = _nueva_operacion(tenant_id)
    async with abrir_tx() as tx:
        if not _es_la_ultima(tenant_id, numero):
            raise ErrorLogoReemplazado
        settings = await _guardar_ruta(tx.conn, tenant_id, None)
    try:
        await cliente.borrar(tenant_id)
    except AlmacenamientoNoDisponible:
        _log.warning(
            "El logo se quitó de la base pero no se pudo borrar de Storage",
            extra={"motivo": "logo_huerfano", "tenant_id": str(tenant_id)},
        )
    return settings


async def modificar_apariencia(abrir_tx: AbrirTx, cambios: dict[str, Any]) -> dict[str, Any]:
    """Colores (#RRGGBB) y plazo de cancelación (0-720 h); `None` borra la clave. Una sola transacción."""
    for clave, valor in cambios.items():
        try:
            validar_valor_de_setting(clave, valor)
        except ValueError as e:
            raise ErrorApariencia(str(e)) from None
    async with abrir_tx() as tx:
        return await settings_local.modificar_settings(tx.conn, tx.sesion.tenant_id, cambios)
