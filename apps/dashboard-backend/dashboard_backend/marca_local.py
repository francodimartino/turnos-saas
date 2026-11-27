"""Dominio de la marca del local (ARCHITECTURE.md §2.12): logo, colores y plazo de cancelación. Sin FastAPI.

El logo vive en Supabase Storage y su ruta en `tenants.settings.logo_ruta`. Cada subida usa un nombre de objeto único
(`<tenant>/logo-<id>.<ext>`, id aleatorio), así que ninguna operación pisa ni borra el objeto que otra acaba de guardar.
Storage nunca se toca dentro de una transacción:

  - Subir: (1) se sube el objeto nuevo; (2) una transacción con `pg_advisory_xact_lock` por tenant lee la ruta
    anterior y guarda la nueva; (3) tras el COMMIT se borra el objeto anterior y se limpia lo viejo que no sea el
    `logo_ruta` vigente, releyéndolo. Si la base falla en (2), `logo_ruta` sigue apuntando al logo anterior (que no se
    tocó) y el objeto nuevo queda huérfano: se registra en el log (`logo_huerfano`, con su ruta).
  - Borrar: (1) una transacción con el mismo lock lee la ruta vigente y quita la clave; (2) tras el COMMIT se borra ese
    objeto. Si Storage falla, queda un huérfano que se registra; nunca una ruta colgada.
  - Si la subida no recibe respuesta (plazo agotado, conexión cortada) el objeto pudo quedar guardado: se registra como
    posible huérfano (`logo_huerfano_posible`) y el pedido responde 503.

La limpieza de lo viejo (`borrar_anteriores`) solo toca objetos con más de `GRACIA_LIMPIEZA_SEG` de antigüedad, para no
borrar el de una subida en curso de otra instancia o de otro pedido que todavía no guardó su ruta. Los huérfanos
registrados se barren en la próxima subida o borrado del mismo negocio, pasada esa gracia.

Cada transacción corta vuelve a verificar la sesión viva y el permiso, y escribe con su auditoría (`settings_updated`,
solo con los nombres de las claves tocadas). Los errores de Storage se propagan como `ErrorAlmacenamiento` (mensaje
fijo, sin la respuesta del proveedor ni la clave de servicio); la ruta los traduce a HTTP.
"""

import logging
from collections.abc import Callable
from typing import Any
from uuid import UUID

from turnos_almacenamiento import (
    AlmacenamientoNoDisponible,
    AlmacenamientoSinRespuesta,
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
SQL_BLOQUEO_LOGO = "select pg_advisory_xact_lock(hashtextextended($1, 0))"

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


def _huerfano(motivo: str, tenant_id: UUID, ruta: str | None) -> None:
    _log.warning(
        "Objeto de logo sin ruta guardada en la base",
        extra={"motivo": motivo, "tenant_id": str(tenant_id), "ruta": ruta},
    )


async def _guardar_ruta(conn: Any, tenant_id: UUID, valor: str | None) -> tuple[dict[str, Any], str | None]:
    """Guarda (o quita, con None) `logo_ruta` bajo el lock del negocio; devuelve los settings y la ruta anterior."""
    await conn.execute(SQL_BLOQUEO_LOGO, f"logo:{tenant_id}")
    anterior = (await settings_local.leer_settings(conn, tenant_id)).get(CLAVE_LOGO)
    settings = await settings_local.modificar_settings(conn, tenant_id, {CLAVE_LOGO: valor})
    return settings, anterior if isinstance(anterior, str) else None


async def _limpiar(cliente: ClienteAlmacenamiento, abrir_tx: AbrirTx, tenant_id: UUID, reemplazada: str | None) -> None:
    """Después del COMMIT: borra el objeto reemplazado y barre lo viejo que no sea el `logo_ruta` vigente."""
    try:
        if reemplazada is not None:
            await cliente.borrar_ruta(tenant_id, reemplazada)
    except AlmacenamientoNoDisponible:
        _huerfano("logo_huerfano", tenant_id, reemplazada)
    try:
        async with abrir_tx() as tx:  # se relee el vigente: otra operación pudo cambiarlo mientras tanto
            vigente = (await settings_local.leer_settings(tx.conn, tenant_id)).get(CLAVE_LOGO)
        await cliente.borrar_anteriores(tenant_id, vigente if isinstance(vigente, str) else None)
    except AlmacenamientoNoDisponible:
        _log.warning("No se pudo limpiar los logos viejos", extra={"motivo": "limpieza_fallida"})


async def subir_logo(
    abrir_tx: AbrirTx, almacenamiento: ClienteAlmacenamiento | None, contenido: bytes
) -> dict[str, Any]:
    """Sube el logo (tipo y tamaño los valida el cliente por los bytes) y guarda su ruta. Devuelve los settings."""
    cliente = _requerir(almacenamiento)
    async with abrir_tx() as tx:  # permiso y tenant de la sesión viva; la transacción se cierra antes de Storage
        tenant_id = tx.sesion.tenant_id
        formato = detectar_formato(contenido)
        if formato is not None:
            # Si la mezcla no entra en el tope de settings, se rechaza antes de subir nada (el id no cambia el largo).
            actuales = await settings_local.leer_settings(tx.conn, tenant_id)
            settings_local.mezclar(actuales, {CLAVE_LOGO: ruta_logo(tenant_id, formato, "0" * 32)})
    try:
        ruta = await cliente.subir(tenant_id, contenido)
    except AlmacenamientoSinRespuesta as e:
        _huerfano("logo_huerfano_posible", tenant_id, e.ruta)
        raise
    try:
        async with abrir_tx() as tx:
            settings, anterior = await _guardar_ruta(tx.conn, tenant_id, ruta)
    except Exception:
        _huerfano("logo_huerfano", tenant_id, ruta)
        raise
    await _limpiar(cliente, abrir_tx, tenant_id, anterior if anterior != ruta else None)
    return settings


async def borrar_logo(abrir_tx: AbrirTx, almacenamiento: ClienteAlmacenamiento | None) -> dict[str, Any]:
    """Quita `logo_ruta` y, tras el COMMIT, borra el objeto que tenía. Es idempotente."""
    cliente = _requerir(almacenamiento)
    async with abrir_tx() as tx:
        settings, anterior = await _guardar_ruta(tx.conn, tx.sesion.tenant_id, None)
        tenant_id = tx.sesion.tenant_id
    await _limpiar(cliente, abrir_tx, tenant_id, anterior)
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
