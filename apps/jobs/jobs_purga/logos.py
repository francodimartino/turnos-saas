"""Borrado del logo de un tenant en Supabase Storage durante la purga (ARCHITECTURE.md §2.2 y §2.12).

La cascada de `platform_admin` recibe un `borrar_logo(tenant_id, ruta)`; este módulo lo arma con el cliente de
`turnos_almacenamiento`:

- el cliente HTTP se crea una sola vez al arrancar el job, con la URL y la clave de servicio de la configuración y
  un timeout explícito; la clave no se loguea ni aparece en `repr` ni en los errores;
- un 404 (el objeto ya no estaba: un reintento después de un borrado que sí se aplicó, o un logo borrado por el
  negocio) es éxito; cualquier otra falla (5xx, timeout, corte de red) lanza y la purga del tenant queda sin marcar
  como `purged`, así que la corrida siguiente vuelve a recibir la ruta de la base y reintenta;
- la ruta se valida contra la carpeta del tenant (`<tenant_id>/logo-<id>.<ext>`): una ruta con otra forma o de otra
  carpeta lanza sin pedirle nada a Storage;
- sin `SUPABASE_SERVICE_ROLE_KEY` el borrador se arma igual (un tenant sin logo se purga sin tocar Storage), pero
  al intentar borrar un logo lanza `ConfiguracionLogoFaltante` con un mensaje que nombra la variable.
"""

import logging
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from turnos_almacenamiento import ClienteAlmacenamiento, ObjetoNoEncontrado

log = logging.getLogger("jobs_purga.logos")

BUCKET_LOGOS = "logos"
TIMEOUT_STORAGE_SEG = 10.0


class ConfiguracionLogoFaltante(RuntimeError):
    """El tenant tiene logo pero el job no tiene la clave de Storage: no se puede borrar."""


class BorradorLogos:
    """`borrar_logo` de la cascada de purga. Sin cliente (config sin clave) falla al intentar borrar un logo."""

    def __init__(self, cliente: ClienteAlmacenamiento | None):
        self._cliente = cliente

    def __repr__(self) -> str:
        return f"BorradorLogos(configurado={self._cliente is not None})"

    async def __call__(self, tenant_id: UUID, ruta: str) -> None:
        if self._cliente is None:
            raise ConfiguracionLogoFaltante(
                "El tenant tiene un logo en Storage y falta la variable SUPABASE_SERVICE_ROLE_KEY para borrarlo"
            )
        try:
            await self._cliente.borrar_ruta(tenant_id, ruta)
        except ObjetoNoEncontrado:
            log.info("logo_ya_borrado", extra={"tenant_id": str(tenant_id)})  # 404: ya no estaba, es éxito

    async def cerrar(self) -> None:
        if self._cliente is not None:
            await self._cliente.cerrar()


def borrador_desde_entorno(entorno: Mapping[str, str], *, http: Any = None) -> BorradorLogos:
    """Lee `SUPABASE_URL` y `SUPABASE_SERVICE_ROLE_KEY` de `entorno` (el del proceso, al arrancar) y arma el cliente.

    Con clave y sin URL (o con una URL insegura o inválida) lanza al arrancar, nombrando la variable. Sin clave
    devuelve un borrador que solo falla si hay que borrar un logo. `http` permite inyectar el cliente HTTP.
    """
    url = (entorno.get("SUPABASE_URL") or "").strip()
    clave = (entorno.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not clave:
        return BorradorLogos(None)
    if not url:
        raise ValueError("Falta la variable SUPABASE_URL para borrar logos en Storage")
    kwargs: dict[str, Any] = {"bucket": BUCKET_LOGOS, "timeout": TIMEOUT_STORAGE_SEG}
    if http is not None:
        kwargs["http"] = http
    return BorradorLogos(ClienteAlmacenamiento(url, clave, **kwargs))
