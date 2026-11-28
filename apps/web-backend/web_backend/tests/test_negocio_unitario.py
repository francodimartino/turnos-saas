"""GET /negocios/{slug}: qué se resuelve, qué se expone y cómo responde cuando el negocio no está disponible."""

import re

import pytest
from turnos_almacenamiento import AlmacenamientoNoDisponible, ConfiguracionAlmacenamientoInvalida

from web_backend import consultas
from web_backend.negocio import CLAVES_PUBLICAS_DE_SETTINGS
from web_backend.tests.soporte import (
    LOGO_A,
    PROFESIONAL_1,
    PROFESIONAL_2,
    SERVICIO_1,
    SERVICIO_2,
    TENANT_A,
    TENANT_B,
    AlmacenamientoFalso,
    app_de_prueba,
    fila_catalogo,
    http,
    negocio_vacio,
)

NO_DISPONIBLE = {
    "detail": {"codigo": "no_disponible", "mensaje": "Este negocio no está disponible."},
}


async def test_la_pagina_devuelve_nombre_marca_direccion_servicios_y_profesionales():
    app, pool = app_de_prueba(almacenamiento=AlmacenamientoFalso())
    pool.conn.catalogo = [
        fila_catalogo(SERVICIO_1, PROFESIONAL_1, nombre_s="Corte", duracion=30, precio="1500", nombre_p="Ana"),
        fila_catalogo(SERVICIO_1, PROFESIONAL_2, nombre_s="Corte", duracion=30, precio="1500", nombre_p="Beto"),
        fila_catalogo(SERVICIO_2, PROFESIONAL_1, nombre_s="Color", duracion=90, precio="4500.50", nombre_p="Ana"),
    ]
    r = await http(app).get("/negocios/sol")
    assert r.status_code == 200
    assert r.json() == {
        "nombre": "Estética Sol",
        "slug": "sol",
        "logo": f"https://storage.ejemplo.test/object/sign/logos/{LOGO_A}?token=firmado",
        "color_principal": "#112233",
        "color_secundario": "#AABBCC",
        "direccion": "Av. Siempreviva 742",
        "zona_horaria": "America/Argentina/Buenos_Aires",
        "servicios": [
            {
                "id": str(SERVICIO_1),
                "nombre": "Corte",
                "duracion_minutos": 30,
                "precio": 1500,
                "profesionales": [str(PROFESIONAL_1), str(PROFESIONAL_2)],
            },
            {
                "id": str(SERVICIO_2),
                "nombre": "Color",
                "duracion_minutos": 90,
                "precio": 4500.5,
                "profesionales": [str(PROFESIONAL_1)],
            },
        ],
        "profesionales": [
            {"id": str(PROFESIONAL_1), "nombre": "Ana", "servicios": [str(SERVICIO_1), str(SERVICIO_2)]},
            {"id": str(PROFESIONAL_2), "nombre": "Beto", "servicios": [str(SERVICIO_1)]},
        ],
    }


async def test_un_negocio_sin_marca_ni_catalogo_devuelve_null_y_listas_vacias():
    app, pool = app_de_prueba()
    pool.conn.negocio = negocio_vacio()
    pool.conn.catalogo = []
    r = await http(app).get("/negocios/sol")
    assert r.status_code == 200
    assert r.json() == {
        "nombre": "Barbería Luna",
        "slug": "sol",
        "logo": None,
        "color_principal": None,
        "color_secundario": None,
        "direccion": None,
        "zona_horaria": "America/Argentina/Buenos_Aires",
        "servicios": [],
        "profesionales": [],
    }


async def test_las_consultas_se_hacen_con_el_tenant_resuelto_y_dentro_de_su_contexto():
    app, pool = app_de_prueba()
    await http(app).get("/negocios/sol")
    contexto = [a for s, a in pool.conn.ejecutado if "set_config('app.current_tenant_id'" in s]
    assert contexto and contexto[0][0] == str(TENANT_A)
    for sql, args in pool.conn.ejecutado:
        if "from public.tenants" in sql or "from public.professional_services" in sql:
            assert args[0] == TENANT_A and TENANT_B not in args
    sentencias = pool.conn.sentencias()
    assert sentencias.index(next(s for s in sentencias if "set_config" in s)) < sentencias.index(
        next(s for s in sentencias if "from public.tenants" in s)
    )


async def test_el_slug_se_resuelve_solo_con_la_funcion_publica():
    app, pool = app_de_prueba()
    await http(app).get("/negocios/sol")
    resolver = [s for s in pool.conn.sentencias() if "resolver_slug_tenant" in s]
    assert resolver == [consultas.SQL_RESOLVER_SLUG]
    assert "public.resolver_slug_tenant($1)" in consultas.SQL_RESOLVER_SLUG
    # Ninguna otra consulta busca un negocio por slug: ni `tenants` ni `slugs_anteriores`.
    otras = [s for s in pool.conn.sentencias() if "slug" in s.lower() and s != consultas.SQL_RESOLVER_SLUG]
    assert otras == []


async def test_el_slug_se_pasa_como_parametro_nunca_dentro_del_sql():
    app, pool = app_de_prueba()
    await http(app).get("/negocios/tienda-ana")
    sql, args = next((s, a) for s, a in pool.conn.ejecutado if "resolver_slug_tenant" in s)
    assert args == ("tienda-ana",) and "tienda" not in sql


# --- Settings: lista explícita ---------------------------------------------------------------------------------


def test_el_sql_del_negocio_lee_solo_las_claves_publicas_de_settings():
    claves = set(re.findall(r"settings\s*->>?\s*'(\w+)'", consultas.SQL_NEGOCIO))
    assert claves == set(CLAVES_PUBLICAS_DE_SETTINGS)
    assert set(CLAVES_PUBLICAS_DE_SETTINGS) == {"local_direccion", "color_principal", "color_secundario", "logo_ruta"}
    # Nunca se trae `settings` entero ni columnas de cobro o de ciclo de vida.
    seleccion = consultas.SQL_NEGOCIO.split("from public.tenants")[0]
    assert not re.search(r"settings\s*(,|$)", seleccion)
    for prohibida in ("plan", "status", "paid_until", "billing_notes", "deactivated_at", "slug"):
        assert not re.search(rf"\b{prohibida}\b", seleccion)


async def test_la_respuesta_no_trae_mas_campos_que_los_documentados():
    app, pool = app_de_prueba(almacenamiento=AlmacenamientoFalso())
    # Aunque la fila trajera de más (no lo hace: el SQL elige las columnas), el modelo de salida lo descarta.
    pool.conn.negocio = {**pool.conn.negocio, "plan": "pilot", "booking_max_per_hour": "3", "local_contacto": "x"}
    cuerpo = (await http(app).get("/negocios/sol")).json()
    assert set(cuerpo) == {
        "nombre",
        "slug",
        "logo",
        "color_principal",
        "color_secundario",
        "direccion",
        "zona_horaria",
        "servicios",
        "profesionales",
    }
    # El id del negocio no sale (salvo dentro de la ruta del objeto de Storage que ya firma la URL del logo).
    assert str(TENANT_A) not in str({k: v for k, v in cuerpo.items() if k != "logo"})
    for servicio in cuerpo["servicios"]:
        assert set(servicio) == {"id", "nombre", "duracion_minutos", "precio", "profesionales"}
    for profesional in cuerpo["profesionales"]:
        assert set(profesional) == {"id", "nombre", "servicios"}


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("color_principal", "rojo"),
        ("color_principal", "#12345"),
        ("color_secundario", "#GGGGGG"),
        ("color_secundario", "#112233\n"),
        ("direccion", "x" * 501),
        ("logo_ruta", f"{TENANT_B}/logo-{'a' * 32}.png"),  # el logo de otro negocio
        ("logo_ruta", "../../otro/logo.png"),
    ],
)
async def test_un_valor_invalido_en_settings_sale_como_null(campo, valor):
    app, pool = app_de_prueba(almacenamiento=AlmacenamientoFalso())
    pool.conn.negocio = {**pool.conn.negocio, campo: valor}
    r = await http(app).get("/negocios/sol")
    assert r.status_code == 200
    clave = "logo" if campo == "logo_ruta" else campo
    assert r.json()[clave] is None


async def test_el_logo_de_otro_negocio_no_se_firma():
    almacenamiento = AlmacenamientoFalso()
    app, pool = app_de_prueba(almacenamiento=almacenamiento)
    pool.conn.negocio = {**pool.conn.negocio, "logo_ruta": f"{TENANT_B}/logo-{'a' * 32}.png"}
    await http(app).get("/negocios/sol")
    assert almacenamiento.llamadas == []


# --- Logo en un bucket privado ---------------------------------------------------------------------------------


async def test_el_logo_se_firma_con_el_tenant_resuelto_y_un_plazo_corto():
    almacenamiento = AlmacenamientoFalso()
    app, _ = app_de_prueba(almacenamiento=almacenamiento)
    await http(app).get("/negocios/sol")
    assert almacenamiento.llamadas == [(TENANT_A, LOGO_A, 3600)]


async def test_el_logo_se_firma_fuera_de_la_transaccion_de_la_base():
    eventos: list[str] = []

    class Almacenamiento(AlmacenamientoFalso):
        async def url_publica_o_firmada(self, tenant_id, ruta, *, expira_seg=3600):
            eventos.append("storage")
            return await super().url_publica_o_firmada(tenant_id, ruta, expira_seg=expira_seg)

    app, pool = app_de_prueba(almacenamiento=Almacenamiento())
    execute = pool.conn.execute

    async def execute_registrando(sql, *args):
        eventos.append(sql)
        await execute(sql, *args)

    pool.conn.execute = execute_registrando
    await http(app).get("/negocios/sol")
    assert eventos.index("storage") > eventos.index("COMMIT")


@pytest.mark.parametrize("error", [AlmacenamientoNoDisponible(), ConfiguracionAlmacenamientoInvalida("x")])
async def test_si_storage_falla_la_pagina_sale_igual_sin_logo(error):
    app, _ = app_de_prueba(almacenamiento=AlmacenamientoFalso(error))
    r = await http(app).get("/negocios/sol")
    assert r.status_code == 200 and r.json()["logo"] is None
    assert r.json()["nombre"] == "Estética Sol"


async def test_sin_cliente_de_storage_la_pagina_sale_sin_logo():
    app, _ = app_de_prueba(almacenamiento=None)
    r = await http(app).get("/negocios/sol")
    assert r.status_code == 200 and r.json()["logo"] is None


async def test_un_negocio_sin_logo_no_llama_a_storage():
    almacenamiento = AlmacenamientoFalso()
    app, pool = app_de_prueba(almacenamiento=almacenamiento)
    pool.conn.negocio = negocio_vacio()
    await http(app).get("/negocios/sol")
    assert almacenamiento.llamadas == []


# --- No disponible -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "slug",
    [
        "no-existe",  # inexistente
        "suspendido",  # tenant suspendido, inactivo o purgado: la función no lo devuelve
        "admin",  # reservado: ningún negocio puede tenerlo
        "reservas-api",
        "a" * 99,  # largo posible pero que no existe
    ],
)
async def test_un_slug_que_no_resuelve_da_404_no_disponible(slug):
    app, _ = app_de_prueba()
    r = await http(app).get(f"/negocios/{slug}")
    assert r.status_code == 404
    assert r.json() == NO_DISPONIBLE


async def test_inexistente_y_suspendido_no_se_distinguen():
    app, _ = app_de_prueba()
    inexistente = await http(app).get("/negocios/no-existe")
    suspendido = await http(app).get("/negocios/suspendido")
    assert (inexistente.status_code, inexistente.json()) == (suspendido.status_code, suspendido.json())
    assert inexistente.headers.get("content-length") == suspendido.headers.get("content-length")


@pytest.mark.parametrize("slug", ["Sol", "so_l", "so%20l", "%E2%98%83", "so.l", "sol%27--", "%3Cscript%3E"])
async def test_un_slug_con_forma_invalida_ni_siquiera_consulta_la_base(slug):
    app, pool = app_de_prueba()
    r = await http(app).get(f"/negocios/{slug}")
    assert r.status_code == 404 and r.json() == NO_DISPONIBLE
    assert pool.adquisiciones == 0


async def test_la_respuesta_de_no_disponible_no_repite_el_slug():
    app, _ = app_de_prueba()
    r = await http(app).get("/negocios/slug-que-no-esta")
    assert "slug-que-no-esta" not in r.text


async def test_un_slug_demasiado_largo_se_rechaza_con_4xx_sin_tocar_la_base():
    app, pool = app_de_prueba()
    r = await http(app).get("/negocios/" + "a" * 101)
    assert r.status_code == 422
    assert r.json()["detail"]["codigo"] == "parametros_invalidos"
    assert r.json()["detail"]["campos"] == ["slug"]
    assert pool.adquisiciones == 0
    assert "aaaa" not in r.text


# --- Slug viejo --------------------------------------------------------------------------------------------------


async def test_un_slug_anterior_redirige_al_vigente():
    app, pool = app_de_prueba()
    r = await http(app).get("/negocios/sol-viejo", follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == "/negocios/sol"
    assert r.json()["detail"] == {
        "codigo": "slug_redirigido",
        "mensaje": "El negocio cambió su dirección.",
        "slug_vigente": "sol",
    }
    # Sin leer nada del negocio: solo se resolvió el slug.
    assert [s for s in pool.conn.sentencias() if "from public.tenants" in s] == []


async def test_el_cliente_que_sigue_la_redireccion_llega_a_la_pagina_vigente():
    app, _ = app_de_prueba()
    r = await http(app).get("/negocios/sol-viejo", follow_redirects=True)
    assert r.status_code == 200 and r.json()["slug"] == "sol"
    assert str(r.url).endswith("/negocios/sol")


async def test_la_redireccion_vuelve_a_validar_el_slug_vigente_que_devuelve_la_base():
    app, pool = app_de_prueba()
    pool.conn.slugs["raro"] = {
        "tenant_id": TENANT_A,
        "nombre": "x",
        "slug_vigente": "../evil?x=1",
        "reemplazado": True,
    }
    r = await http(app).get("/negocios/raro", follow_redirects=False)
    assert r.status_code == 404 and r.json() == NO_DISPONIBLE


async def test_un_slug_con_barra_no_matchea_ninguna_ruta_y_da_404_con_codigo():
    app, pool = app_de_prueba()
    r = await http(app).get("/negocios/sol%2F..")
    assert r.status_code == 404
    assert r.json()["detail"]["codigo"] == "no_encontrado"
    assert pool.adquisiciones == 0
