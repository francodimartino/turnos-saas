"""Dominio de la marca del local (ARCHITECTURE.md §2.12): logo, colores y plazo de cancelación. Sin FastAPI.

El logo vive en Supabase Storage y su ruta en `tenants.settings.logo_ruta`. La subida y el borrado de Storage ocurren
FUERA de toda transacción (una conexión de la base no se retiene mientras se espera a un servicio externo) y la base
solo se toca después de que Storage respondió bien: si Storage falla, `logo_ruta` queda como estaba y nunca apunta a
un logo que no existe. Cada transacción corta vuelve a verificar la sesión viva y el permiso, y escribe con su
auditoría (`settings_updated`, solo con los nombres de las claves tocadas).

Los errores de Storage se propagan como `ErrorAlmacenamiento` (mensaje fijo, sin la respuesta del proveedor ni la clave
de servicio); la ruta los traduce a HTTP.
"""

import logging
from collections.abc import Callable
from typing import Any

from turnos_almacenamiento import ClienteAlmacenamiento, ErrorAlmacenamiento
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
    "borrar_logo",
    "modificar_apariencia",
    "subir_logo",
]


class ErrorAlmacenamientoNoConfigurado(ErrorAlmacenamiento):
    """La app se armó sin cliente de Storage."""

    mensaje = "El servicio de almacenamiento no está disponible"


class ErrorApariencia(Exception):
    """Un valor de apariencia no cumple su regla; el mensaje nombra la clave, nunca el valor."""


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
    ruta = await cliente.subir(tenant_id, contenido)
    try:
        async with abrir_tx() as tx:
            return await settings_local.modificar_settings(tx.conn, tenant_id, {CLAVE_LOGO: ruta})
    except Exception:
        _log.warning("El logo se subió pero no se pudo guardar su ruta", extra={"motivo": "logo_sin_ruta"})
        raise


async def borrar_logo(abrir_tx: AbrirTx, almacenamiento: ClienteAlmacenamiento | None) -> dict[str, Any]:
    """Borra el logo de Storage (cualquier formato) y quita la clave. Es idempotente."""
    cliente = _requerir(almacenamiento)
    async with abrir_tx() as tx:
        tenant_id = tx.sesion.tenant_id
    await cliente.borrar(tenant_id)
    async with abrir_tx() as tx:
        return await settings_local.modificar_settings(tx.conn, tenant_id, {CLAVE_LOGO: None})


async def modificar_apariencia(abrir_tx: AbrirTx, cambios: dict[str, Any]) -> dict[str, Any]:
    """Colores (#RRGGBB) y plazo de cancelación (0-720 h); `None` borra la clave. Una sola transacción."""
    for clave, valor in cambios.items():
        try:
            validar_valor_de_setting(clave, valor)
        except ValueError as e:
            raise ErrorApariencia(str(e)) from None
    async with abrir_tx() as tx:
        return await settings_local.modificar_settings(tx.conn, tx.sesion.tenant_id, cambios)
