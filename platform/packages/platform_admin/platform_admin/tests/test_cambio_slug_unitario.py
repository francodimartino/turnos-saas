"""Cambio de slug de un tenant: validación previa, traducción de los errores de la base y transacción, sin base real."""

import uuid

import pytest
from platform_tenancy.testing import TransaccionPorExecute

from platform_admin import (
    CambioSlugConcurrente,
    DatosTenantInvalidos,
    PurgaEnCurso,
    SlugEnUso,
    SlugInvalido,
    SlugReservado,
    SlugSinCambio,
    TenantNoEncontrado,
    TransicionInvalida,
    cambiar_slug,
)
from platform_admin.tests.falsos import PoolFalso

ACTOR = uuid.UUID("22222222-2222-2222-2222-222222222222")
TENANT = uuid.UUID("33333333-3333-3333-3333-333333333333")


class ErrorBase(Exception):
    def __init__(self, sqlstate):
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


class ConexionSlugs(TransaccionPorExecute):
    """Imita las reglas de `cambiar_slug_tenant` (mismos SQLSTATE que la base real) sobre un estado en memoria."""

    def __init__(self, slug="mi-negocio", status="active", existe=True, ocupados=(), falla=None):
        self.tenant = {"slug": slug, "status": status} if existe else None
        self.ocupados = set(ocupados)  # slugs de otros tenants o anteriores vigentes de otro negocio
        self.falla = falla  # SQLSTATE a lanzar al ejecutar la función, tras pasar sus validaciones
        self.comandos: list[tuple[str, tuple]] = []

    async def execute(self, sql, *args):
        self.comandos.append((sql, args))
        return "OK"

    async def fetchrow(self, sql, *args):
        self.comandos.append((sql, args))
        return dict(self.tenant) if self.tenant else None

    async def fetchval(self, sql, *args):
        self.comandos.append((sql, args))
        if "cambiar_slug_tenant" not in sql:
            return None
        nuevo = args[1]
        if self.tenant is None:
            raise ErrorBase("P0002")
        if self.tenant["status"] in ("purging", "purged"):
            raise ErrorBase("23514")
        if nuevo == self.tenant["slug"]:
            raise ErrorBase("22023")
        if nuevo in self.ocupados:
            raise ErrorBase("23505")
        if self.falla:
            raise ErrorBase(self.falla)
        self.tenant["slug"] = nuevo
        return nuevo

    def llamadas_a_la_funcion(self):
        return [c for c in self.comandos if "cambiar_slug_tenant" in c[0]]


async def _cambiar(conn, slug):
    pool = PoolFalso(conn)
    return await cambiar_slug(pool, TENANT, slug, actor_id=ACTOR)


async def test_cambia_el_slug_llamando_a_la_funcion_de_la_base_con_el_actor_y_devuelve_el_anterior():
    conn = ConexionSlugs()
    res = await _cambiar(conn, "nuevo-nombre")

    assert (res.tenant_id, res.slug_anterior, res.slug_nuevo) == (TENANT, "mi-negocio", "nuevo-nombre")
    ((sql, args),) = conn.llamadas_a_la_funcion()
    assert "public.cambiar_slug_tenant($1, $2)" in sql and args == (TENANT, "nuevo-nombre")
    # El actor lo fija admin_tx (app.current_actor_id) y la base audita: acá no se escribe audit_log a mano.
    assert any("app.current_actor_id" in c[0] and str(ACTOR) in map(str, c[1]) for c in conn.comandos)
    assert not any("audit_log" in c[0] for c in conn.comandos)


@pytest.mark.parametrize(
    "slug",
    [
        None,
        5,
        "",
        "ab",
        "a" * 41,
        "Mayuscula",
        "con espacio",
        "con_guion_bajo",
        "-empieza",
        "termina-",
        "doble--guion",
        "ñandú",
    ],
)
async def test_formato_o_largo_invalidos_son_slug_invalido_y_no_tocan_la_base(slug):
    conn = ConexionSlugs()
    with pytest.raises(SlugInvalido):
        await _cambiar(conn, slug)
    assert conn.comandos == []


@pytest.mark.parametrize("slug", ["abc", "a" * 40, "a1-b2"])
async def test_los_bordes_validos_se_aceptan(slug):
    assert (await _cambiar(ConexionSlugs(), slug)).slug_nuevo == slug


@pytest.mark.parametrize("slug", ["admin", "api", "reservas-api", "www"])
async def test_un_slug_reservado_es_slug_reservado_y_no_toca_la_base(slug):
    conn = ConexionSlugs()
    with pytest.raises(SlugReservado):
        await _cambiar(conn, slug)
    assert conn.comandos == []


def test_los_errores_de_slug_son_errores_de_datos_del_alta():
    assert issubclass(SlugInvalido, DatosTenantInvalidos) and issubclass(SlugReservado, DatosTenantInvalidos)


async def test_tenant_inexistente_es_tenant_no_encontrado_sin_llamar_a_la_funcion():
    conn = ConexionSlugs(existe=False)
    with pytest.raises(TenantNoEncontrado):
        await _cambiar(conn, "otro-nombre")
    assert conn.llamadas_a_la_funcion() == []


async def test_el_mismo_slug_vigente_es_slug_sin_cambio():
    conn = ConexionSlugs(slug="mi-negocio")
    with pytest.raises(SlugSinCambio):
        await _cambiar(conn, "mi-negocio")
    assert conn.llamadas_a_la_funcion() == []


async def test_un_tenant_en_purga_es_purga_en_curso_y_uno_purgado_es_transicion_invalida():
    with pytest.raises(PurgaEnCurso):
        await _cambiar(ConexionSlugs(status="purging"), "otro-nombre")
    with pytest.raises(TransicionInvalida) as e:
        await _cambiar(ConexionSlugs(status="purged"), "otro-nombre")
    assert not isinstance(e.value, PurgaEnCurso)


async def test_slug_en_uso_por_otro_tenant_o_como_enlace_anterior_vigente_es_slug_en_uso():
    conn = ConexionSlugs(ocupados={"ocupado"})
    with pytest.raises(SlugEnUso):
        await _cambiar(conn, "ocupado")
    assert conn.tenant["slug"] == "mi-negocio"


@pytest.mark.parametrize("sqlstate", ["40P01", "40001", "55P03"])
async def test_un_bloqueo_mutuo_o_conflicto_de_serializacion_es_cambio_concurrente(sqlstate):
    with pytest.raises(CambioSlugConcurrente):
        await _cambiar(ConexionSlugs(falla=sqlstate), "otro-nombre")


@pytest.mark.parametrize(
    ("sqlstate", "error"),
    [("23505", SlugEnUso), ("P0002", TenantNoEncontrado), ("22023", SlugInvalido), ("23514", SlugReservado)],
)
async def test_si_la_base_rechaza_igual_tras_la_validacion_previa_se_traduce_por_sqlstate(sqlstate, error):
    # Carrera: la validación previa pasó y la base (que es la que manda) lo rechaza por su cuenta.
    with pytest.raises(error):
        await _cambiar(ConexionSlugs(falla=sqlstate), "otro-nombre")


async def test_un_error_inesperado_de_la_base_no_se_disfraza():
    with pytest.raises(ErrorBase):
        await _cambiar(ConexionSlugs(falla="XX000"), "otro-nombre")
