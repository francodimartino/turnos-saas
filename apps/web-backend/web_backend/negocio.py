"""Dominio de la página pública de un negocio (ARCHITECTURE.md §2.12): sin FastAPI.

Resolver el slug (`resolver_slug`), leer lo que se muestra del negocio (`cargar_pagina`) y armar la URL de su logo
(`url_del_logo`). Cada función de lectura recibe la conexión ya abierta por quien llama; la de la página, dentro de la
transacción con el contexto del tenant resuelto. De `tenants.settings` solo sale lo que está en
`CLAVES_PUBLICAS_DE_SETTINGS`, y cada valor se revalida: lo que no cumple el formato sale como `None` en vez de
llegar al cliente.
"""

import logging
import re
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from turnos_almacenamiento import ErrorAlmacenamiento, url_publica_logo
from turnos_dominio.settings_tenant import CLAVE_LOGO, CLAVES_COLOR, validar_valor_de_setting

from web_backend import consultas

_log = logging.getLogger("web_backend.negocio")

# Un slug en minúsculas con guiones internos (el formato que impone la base al cambiarlo). `\Z` y no `$`: `$` acepta un
# salto de línea final.
PATRON_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
# El alta de un negocio no limita el largo del slug; este es el tope de lo que se acepta en la URL.
MAX_LARGO_SLUG = 100

# Lo único de `tenants.settings` que sale por la API pública. Cualquier otra clave (límites de reserva, contacto,
# horarios, campos extra, notas) queda adentro.
CLAVE_DIRECCION = "local_direccion"
CLAVES_PUBLICAS_DE_SETTINGS = (CLAVE_DIRECCION, *CLAVES_COLOR, CLAVE_LOGO)

# `deposit` no se ofrece: el piloto no cobra señas.
MODOS_RESERVABLES = ("auto", "approval")


class ErrorNegocioNoDisponible(Exception):
    """El negocio no se puede mostrar: no existe, está suspendido o dado de baja (la base no los distingue)."""


@dataclass(frozen=True)
class SlugResuelto:
    tenant_id: uuid.UUID
    slug_vigente: str
    reemplazado: bool  # el slug pedido era uno anterior: hay que redirigir al vigente


@dataclass(frozen=True)
class ServicioPublico:
    id: uuid.UUID
    nombre: str
    duracion_minutos: int
    precio: Decimal
    profesionales: tuple[uuid.UUID, ...]


@dataclass(frozen=True)
class ProfesionalPublico:
    id: uuid.UUID
    nombre: str
    servicios: tuple[uuid.UUID, ...]


@dataclass(frozen=True)
class PaginaNegocio:
    nombre: str
    direccion: str | None
    color_principal: str | None
    color_secundario: str | None
    logo_ruta: str | None
    servicios: tuple[ServicioPublico, ...]
    profesionales: tuple[ProfesionalPublico, ...]


def slug_con_forma_valida(slug: str) -> bool:
    return len(slug) <= MAX_LARGO_SLUG and PATRON_SLUG.match(slug) is not None


async def resolver_slug(conn: Any, slug: str) -> SlugResuelto | None:
    """Resuelve el slug con la función pública de la base; `None` si no hay un negocio activo con ese slug.

    Un slug con forma inválida no llega a la base: ningún negocio puede tenerlo.
    """
    if not slug_con_forma_valida(slug):
        return None
    fila = await conn.fetchrow(consultas.SQL_RESOLVER_SLUG, slug)
    if fila is None:
        return None
    vigente = fila["slug_vigente"]
    if not isinstance(vigente, str) or not slug_con_forma_valida(vigente):
        _log.error("slug_vigente_invalido")
        return None
    return SlugResuelto(fila["tenant_id"], vigente, bool(fila["reemplazado"]))


def _setting_valido(clave: str, valor: Any, tenant_id: uuid.UUID) -> str | None:
    """El valor si cumple la regla de su clave; si no (o si es null), `None`."""
    if valor is None:
        return None
    try:
        validar_valor_de_setting(clave, valor, tenant_id)
    except ValueError:
        return None
    return str(valor)


def _armar_catalogo(
    filas: list[Any],
) -> tuple[tuple[ServicioPublico, ...], tuple[ProfesionalPublico, ...]]:
    servicios: dict[uuid.UUID, dict[str, Any]] = {}
    profesionales: dict[uuid.UUID, dict[str, Any]] = {}
    for f in filas:
        s = servicios.setdefault(
            f["servicio_id"],
            {"nombre": f["servicio"], "duracion": f["duration_minutes"], "precio": f["price"], "profesionales": []},
        )
        p = profesionales.setdefault(f["profesional_id"], {"nombre": f["profesional"], "servicios": []})
        s["profesionales"].append(f["profesional_id"])
        p["servicios"].append(f["servicio_id"])
    return (
        tuple(
            ServicioPublico(i, s["nombre"], s["duracion"], s["precio"], tuple(s["profesionales"]))
            for i, s in servicios.items()
        ),
        tuple(ProfesionalPublico(i, p["nombre"], tuple(p["servicios"])) for i, p in profesionales.items()),
    )


async def cargar_pagina(conn: Any, tenant_id: uuid.UUID) -> PaginaNegocio:
    """Lo que muestra la página del negocio. `conn` ya tiene el contexto de `tenant_id`."""
    fila = await conn.fetchrow(consultas.SQL_NEGOCIO, tenant_id)
    if fila is None:
        raise ErrorNegocioNoDisponible
    filas = list(
        await conn.fetch(consultas.SQL_CATALOGO, tenant_id, list(MODOS_RESERVABLES), consultas.TOPE_FILAS_CATALOGO + 1)
    )
    if len(filas) > consultas.TOPE_FILAS_CATALOGO:
        # El catálogo se corta en el tope: se avisa en el log (sin datos del negocio ni de personas).
        _log.warning("catalogo_truncado", extra={"count": consultas.TOPE_FILAS_CATALOGO})
        filas = filas[: consultas.TOPE_FILAS_CATALOGO]
    servicios, profesionales = _armar_catalogo(filas)
    direccion = _setting_valido(CLAVE_DIRECCION, fila["direccion"], tenant_id)
    return PaginaNegocio(
        nombre=fila["nombre"],
        direccion=(direccion.strip() or None) if direccion is not None else None,
        color_principal=_setting_valido("color_principal", fila["color_principal"], tenant_id),
        color_secundario=_setting_valido("color_secundario", fila["color_secundario"], tenant_id),
        logo_ruta=_setting_valido(CLAVE_LOGO, fila["logo_ruta"], tenant_id),
        servicios=servicios,
        profesionales=profesionales,
    )


def url_del_logo(url_supabase: str | None, tenant_id: uuid.UUID, logo_ruta: str | None) -> str | None:
    """URL pública del logo (bucket de solo lectura), o `None` si no hay logo o no hay URL del proyecto.

    Se arma sin red ni secretos a partir de la URL del proyecto y de la ruta ya validada.
    """
    if logo_ruta is None or url_supabase is None:
        return None
    try:
        return url_publica_logo(url_supabase, tenant_id, logo_ruta)
    except ErrorAlmacenamiento as error:
        _log.warning("logo_no_disponible", extra={"error_tipo": type(error).__name__})
        return None
