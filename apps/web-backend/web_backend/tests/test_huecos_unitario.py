"""GET /negocios/{slug}/huecos: parámetros, tope del rango y uso del cálculo de turnos_dominio."""

from datetime import datetime, timedelta, timezone

import pytest
from turnos_dominio import Ventana
from turnos_negocio import ZONA_HORARIA

from web_backend import consultas, huecos
from web_backend.tests.soporte import (
    AHORA,
    PROFESIONAL_1,
    SERVICIO_1,
    TENANT_A,
    app_de_prueba,
    http,
)

UTC = timezone.utc  # noqa: UP017 - datetime.UTC no existe en Python 3.10


class CalculoFalso:
    """Reemplaza a `calcular_huecos`: registra con qué se lo llamó y devuelve huecos fijos."""

    def __init__(self, resultado=None):
        self.llamadas: list[tuple] = []
        self.resultado = resultado if resultado is not None else []

    async def __call__(self, conn, profesional_id, servicio_id, rango, **kw):
        self.llamadas.append((conn, profesional_id, servicio_id, rango, kw))
        return self.resultado


@pytest.fixture
def calculo(monkeypatch):
    falso = CalculoFalso()
    monkeypatch.setattr(huecos, "calcular_huecos", falso)
    return falso


def consulta(**cambios):
    p = {
        "servicio_id": str(SERVICIO_1),
        "profesional_id": str(PROFESIONAL_1),
        "desde": "2026-11-02",
        "hasta": "2026-11-04",
    }
    p.update(cambios)
    return p


async def pedir(slug="sol", **cambios):
    app, pool = app_de_prueba()
    r = await http(app).get(f"/negocios/{slug}/huecos", params=consulta(**cambios))
    return r, pool


def local(anio, mes, dia, hora=0, minuto=0):
    return datetime(anio, mes, dia, hora, minuto, tzinfo=ZONA_HORARIA)


async def test_reutiliza_calcular_huecos_con_el_rango_en_hora_local_y_la_anticipacion_por_defecto(calculo):
    hueco = Ventana(local(2026, 11, 3, 10), local(2026, 11, 3, 10, 30))
    calculo.resultado = [hueco]
    r, pool = await pedir()
    assert r.status_code == 200
    assert r.json() == {
        "zona_horaria": "America/Argentina/Buenos_Aires",
        "huecos": [{"inicio": "2026-11-03T10:00:00-03:00", "fin": "2026-11-03T10:30:00-03:00"}],
    }
    ((conn, profesional, servicio, rango, kw),) = calculo.llamadas
    assert conn is pool.conn
    assert (profesional, servicio) == (PROFESIONAL_1, SERVICIO_1)
    # `hasta` es inclusive: el rango llega a la medianoche local del día siguiente.
    assert rango == Ventana(local(2026, 11, 2), local(2026, 11, 5))
    # Sin `booking_min_notice_minutes` en el negocio rige el default del bot: 30 minutos.
    assert kw == {"no_antes_de": AHORA + timedelta(minutes=30)}


async def test_la_anticipacion_minima_sale_de_los_settings_del_negocio(calculo):
    app, pool = app_de_prueba()
    pool.conn.aviso_minimo = "180"
    r = await http(app).get("/negocios/sol/huecos", params=consulta())
    assert r.status_code == 200
    assert calculo.llamadas[0][4] == {"no_antes_de": AHORA + timedelta(minutes=180)}


@pytest.mark.parametrize("valor", ["abc", "-5", "99999", "1.5", ""])
async def test_una_anticipacion_invalida_en_settings_cae_al_default(calculo, valor):
    app, pool = app_de_prueba()
    pool.conn.aviso_minimo = valor
    await http(app).get("/negocios/sol/huecos", params=consulta())
    assert calculo.llamadas[0][4] == {"no_antes_de": AHORA + timedelta(minutes=30)}


async def test_sin_huecos_devuelve_la_lista_vacia(calculo):
    r, _ = await pedir()
    assert r.status_code == 200 and r.json()["huecos"] == []


async def test_el_calculo_corre_dentro_de_la_transaccion_del_tenant_resuelto(calculo):
    _, pool = await pedir()
    contexto = [a for s, a in pool.conn.ejecutado if "set_config('app.current_tenant_id'" in s]
    assert contexto[0][0] == str(TENANT_A)
    for sql, args in pool.conn.ejecutado:
        if sql in (consultas.SQL_PAR_RESERVABLE, consultas.SQL_AVISO_MINIMO):
            assert args[0] == TENANT_A


async def test_un_servicio_o_profesional_que_no_se_puede_reservar_da_404(calculo):
    app, pool = app_de_prueba()
    pool.conn.par_reservable = False
    r = await http(app).get("/negocios/sol/huecos", params=consulta())
    assert r.status_code == 404
    assert r.json()["detail"]["codigo"] == "no_encontrado"
    assert calculo.llamadas == []


# --- Rango y fechas ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("desde", "hasta", "codigo"),
    [
        ("2026-11-04", "2026-11-02", "rango_invalido"),
        ("2026-11-02", "2026-12-03", "rango_demasiado_grande"),  # 32 días
        ("2026-11-01", "2026-11-03", "fecha_fuera_de_rango"),  # empieza antes de hoy
        ("2026-10-01", "2026-10-02", "fecha_fuera_de_rango"),
        ("2027-01-30", "2027-02-01", "fecha_fuera_de_rango"),  # termina más allá de hoy + 90 días
        ("2026-02-30", "2026-03-01", "fecha_invalida"),
        ("2026-13-01", "2026-13-02", "fecha_invalida"),
    ],
)
async def test_los_rangos_inaceptables_se_rechazan_con_su_codigo(calculo, desde, hasta, codigo):
    r, pool = await pedir(desde=desde, hasta=hasta)
    assert r.status_code == 422
    assert r.json()["detail"]["codigo"] == codigo
    assert calculo.llamadas == [] and pool.adquisiciones == 0
    assert desde not in r.text and hasta not in r.text


async def test_el_rango_maximo_de_31_dias_y_el_horizonte_de_90_se_aceptan(calculo):
    r, _ = await pedir(desde="2026-11-02", hasta="2026-12-02")  # 31 días, el primero es hoy
    assert r.status_code == 200
    r, _ = await pedir(desde="2027-01-31", hasta="2027-01-31")  # hoy + 90 días
    assert r.status_code == 200


def test_hoy_se_mide_en_la_zona_del_negocio_no_en_utc():
    # 01:30 UTC del 3 de noviembre es todavía el 2 de noviembre (22:30) en Buenos Aires: ese día aún es "hoy".
    ahora = datetime(2026, 11, 3, 1, 30, tzinfo=UTC)
    rango = huecos.armar_rango(huecos.parsear_fecha("2026-11-02"), huecos.parsear_fecha("2026-11-02"), ahora)
    assert rango == Ventana(local(2026, 11, 2), local(2026, 11, 3))


# --- Entrada: formato y tope de tamaño ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("parametro", "valor"),
    [
        ("servicio_id", "no-es-un-uuid"),
        ("servicio_id", "x" * 200),
        ("profesional_id", "11111111-0000-4000-8000-00000000000"),
        ("profesional_id", str(PROFESIONAL_1) + "0"),
        ("desde", "2026-11-0"),
        ("desde", "2026-11-02T00:00:00"),
        ("desde", "9" * 200),
        ("hasta", "mañana"),
        ("hasta", "０２０２６-１１-０２"),  # dígitos de ancho completo
    ],
)
async def test_un_parametro_mal_formado_o_demasiado_largo_da_422_sin_repetir_lo_recibido(calculo, parametro, valor):
    r, pool = await pedir(**{parametro: valor})
    assert r.status_code == 422
    detalle = r.json()["detail"]
    assert detalle["codigo"] == "parametros_invalidos"
    assert detalle["campos"] == [parametro]
    assert valor not in r.text
    assert pool.adquisiciones == 0 and calculo.llamadas == []


@pytest.mark.parametrize("faltante", ["servicio_id", "profesional_id", "desde", "hasta"])
async def test_un_parametro_obligatorio_ausente_da_422(calculo, faltante):
    app, _ = app_de_prueba()
    params = consulta()
    del params[faltante]
    r = await http(app).get("/negocios/sol/huecos", params=params)
    assert r.status_code == 422
    assert r.json()["detail"] == {
        "codigo": "parametros_invalidos",
        "mensaje": "Los parámetros del pedido no son válidos.",
        "campos": [faltante],
    }


async def test_los_parametros_de_mas_se_ignoran_y_no_se_reflejan(calculo):
    app, _ = app_de_prueba()
    r = await http(app).get(
        "/negocios/sol/huecos", params={**consulta(), "email": "ana@ejemplo.test", "tenant_id": "x"}
    )
    assert r.status_code == 200
    assert "ana@ejemplo.test" not in r.text


async def test_el_tenant_nunca_se_toma_de_la_entrada(calculo):
    app, pool = app_de_prueba()
    otro = "bbbbbbbb-0000-4000-8000-000000000002"
    await http(app).get("/negocios/sol/huecos", params={**consulta(), "tenant_id": otro}, headers={"X-Tenant-Id": otro})
    for _, args in pool.conn.ejecutado:
        assert otro not in [str(a) for a in args]


# --- Slug --------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("slug", ["no-existe", "suspendido", "admin"])
async def test_un_negocio_que_no_resuelve_da_404_no_disponible(calculo, slug):
    r, _ = await pedir(slug=slug)
    assert r.status_code == 404 and r.json()["detail"]["codigo"] == "no_disponible"
    assert calculo.llamadas == []


async def test_un_slug_anterior_redirige_al_mismo_pedido_en_el_slug_vigente(calculo):
    app, _ = app_de_prueba()
    r = await http(app).get("/negocios/sol-viejo/huecos", params=consulta(), follow_redirects=False)
    assert r.status_code == 301
    destino = r.headers["location"]
    assert destino.startswith("/negocios/sol/huecos?")
    for clave, valor in consulta().items():
        assert f"{clave}={valor}" in destino
    assert r.json()["detail"]["slug_vigente"] == "sol"
    assert calculo.llamadas == []


async def test_la_redireccion_no_copia_parametros_que_el_servicio_no_conoce(calculo):
    app, _ = app_de_prueba()
    r = await http(app).get(
        "/negocios/sol-viejo/huecos", params={**consulta(), "email": "ana@ejemplo.test"}, follow_redirects=False
    )
    assert "ana" not in r.headers["location"] and "email" not in r.headers["location"]
