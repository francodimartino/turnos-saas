"""Página pública de un negocio y sus horarios libres (ARCHITECTURE.md §2.12).

  GET /negocios/{slug}                 nombre, logo, colores, dirección, servicios con precio y duración, profesionales
  GET /negocios/{slug}/huecos          horarios libres de un profesional para un servicio, en un rango de fechas

Sin sesión ni cuenta. El slug se resuelve con la función pública de la base (solo negocios activos): uno inexistente,
suspendido, dado de baja o reservado da el mismo 404 `no_disponible`; uno anterior responde 301 al pedido equivalente
en el slug vigente (el cuerpo trae `slug_vigente`). El negocio de cada consulta es el que resolvió la base.

Toda entrada tiene su tope: el slug hasta `MAX_LARGO_SLUG` caracteres, los ids exactamente 36 y las fechas exactamente
10 (`AAAA-MM-DD`); lo que no cumple es 422 `parametros_invalidos` sin repetir lo recibido. Los errores de dominio
del pedido de huecos (`ErrorHuecos`) son 422 con su código, salvo el servicio o profesional que no existe (404).

Este módulo solo traduce HTTP: las reglas, el cálculo y el SQL están en `negocio`, `huecos` y `consultas`.
"""

from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import JSONResponse
from turnos_http import cuerpo_error, error_http
from turnos_negocio import ZONA_HORARIA

from web_backend import huecos, negocio
from web_backend.dependencias import (
    abrir_negocio,
    abrir_sin_contexto,
    config_actual,
    pool_actual,
    reloj_actual,
)
from web_backend.respuestas_negocio import HuecoOut, HuecosOut, NegocioOut, ProfesionalOut, ServicioOut

router = APIRouter(tags=["negocios"])

CODIGO_NO_DISPONIBLE = "no_disponible"
MENSAJE_NO_DISPONIBLE = "Este negocio no está disponible."

# Hexadecimal con guiones, 36 caracteres. `[0-9]` y no `\d`: el motor de patrones de pydantic acepta otros dígitos.
PATRON_UUID = "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
PATRON_FECHA = "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"

Slug = Annotated[str, Path(min_length=1, max_length=negocio.MAX_LARGO_SLUG)]
IdTexto = Annotated[str, Query(min_length=36, max_length=36, pattern=PATRON_UUID)]
FechaTexto = Annotated[str, Query(min_length=10, max_length=10, pattern=PATRON_FECHA)]

Pool = Annotated[Any, Depends(pool_actual)]
Configuracion = Annotated[Any, Depends(config_actual)]
Reloj = Annotated[Any, Depends(reloj_actual)]


def _no_disponible():
    return error_http(404, CODIGO_NO_DISPONIBLE, MENSAJE_NO_DISPONIBLE)


async def _resolver(pool: Any, slug: str) -> negocio.SlugResuelto:
    if not negocio.slug_con_forma_valida(slug):
        raise _no_disponible()  # ningún negocio puede tener ese slug: no hace falta preguntarle a la base
    async with abrir_sin_contexto(pool) as conn:
        resuelto = await negocio.resolver_slug(conn, slug)
    if resuelto is None:
        raise _no_disponible()
    return resuelto


def _redireccion(destino: str, slug_vigente: str) -> JSONResponse:
    return JSONResponse(
        status_code=301,
        content=cuerpo_error("slug_redirigido", "El negocio cambió su dirección.", slug_vigente=slug_vigente),
        headers={"Location": destino},
    )


@router.get("/negocios/{slug}", response_model=NegocioOut)
async def ver_negocio(slug: Slug, pool: Pool, config: Configuracion):
    resuelto = await _resolver(pool, slug)
    if resuelto.reemplazado:
        return _redireccion(f"/negocios/{resuelto.slug_vigente}", resuelto.slug_vigente)
    try:
        async with abrir_negocio(pool, resuelto.tenant_id) as conn:
            pagina = await negocio.cargar_pagina(conn, resuelto.tenant_id)
    except negocio.ErrorNegocioNoDisponible:
        raise _no_disponible() from None
    logo = negocio.url_del_logo(config.supabase_url, resuelto.tenant_id, pagina.logo_ruta)
    return NegocioOut(
        nombre=pagina.nombre,
        slug=resuelto.slug_vigente,
        logo=logo,
        color_principal=pagina.color_principal,
        color_secundario=pagina.color_secundario,
        direccion=pagina.direccion,
        zona_horaria=str(ZONA_HORARIA),
        servicios=[
            ServicioOut(
                id=s.id,
                nombre=s.nombre,
                duracion_minutos=s.duracion_minutos,
                precio=s.precio,
                profesionales=list(s.profesionales),
            )
            for s in pagina.servicios
        ],
        profesionales=[
            ProfesionalOut(id=p.id, nombre=p.nombre, servicios=list(p.servicios)) for p in pagina.profesionales
        ],
    )


@router.get("/negocios/{slug}/huecos", response_model=HuecosOut)
async def ver_huecos(
    slug: Slug,
    servicio_id: IdTexto,
    profesional_id: IdTexto,
    desde: FechaTexto,
    hasta: FechaTexto,
    pool: Pool,
    reloj: Reloj,
):
    ahora = reloj()
    try:
        consulta = huecos.armar_consulta(servicio_id, profesional_id, desde, hasta, ahora)
    except huecos.ErrorHuecos as error:
        raise error_http(422, error.codigo, error.mensaje) from None
    resuelto = await _resolver(pool, slug)
    if resuelto.reemplazado:
        # Se arma desde lo ya validado, no desde el texto recibido: la redirección no refleja parámetros ajenos.
        query = urlencode(
            {
                "servicio_id": str(consulta.servicio_id),
                "profesional_id": str(consulta.profesional_id),
                "desde": desde,
                "hasta": hasta,
            }
        )
        return _redireccion(f"/negocios/{resuelto.slug_vigente}/huecos?{query}", resuelto.slug_vigente)
    try:
        async with abrir_negocio(pool, resuelto.tenant_id) as conn:
            libres = await huecos.buscar_huecos(conn, resuelto.tenant_id, consulta, ahora)
    except huecos.ServicioOProfesionalInexistente as error:
        raise error_http(404, error.codigo, error.mensaje) from None
    return HuecosOut(zona_horaria=str(ZONA_HORARIA), huecos=[HuecoOut(inicio=h.inicio, fin=h.fin) for h in libres])
