import asyncio
import json
import re
import time
import uuid
from datetime import datetime, timezone

import httpx
import pytest

from turnos_almacenamiento import (
    AlmacenamientoNoDisponible,
    AlmacenamientoSinRespuesta,
    ArchivoDemasiadoGrande,
    ClienteAlmacenamiento,
    ConfiguracionAlmacenamientoInvalida,
    ErrorAlmacenamiento,
    ObjetoNoEncontrado,
    TipoNoPermitido,
    ruta_logo,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 20
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 20
SVG = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"></svg>'
TENANT = uuid.UUID("11111111-2222-3333-4444-555555555555")
ID = "0123456789abcdef0123456789abcdef"
CLAVE = "clave-de-servicio-secreta"


def _cliente(handler, **kw):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    return ClienteAlmacenamiento("https://proy.supabase.co", CLAVE, http=http, **kw)


def _ok(peticiones):
    def handler(req: httpx.Request) -> httpx.Response:
        peticiones.append(req)
        return httpx.Response(200, json={"Key": "x", "signedURL": "/object/sign/logos/a?token=t"})

    return handler


def _patron(ext):
    return re.compile(rf"{TENANT}/logo-[0-9a-f]{{32}}\.{ext}\Z")


@pytest.mark.parametrize(
    ("bytes_", "ext", "mime"), [(PNG, "png", "image/png"), (JPG, "jpg", "image/jpeg"), (WEBP, "webp", "image/webp")]
)
async def test_sube_el_formato_detectado_por_bytes_con_un_nombre_unico(bytes_, ext, mime):
    reqs: list[httpx.Request] = []
    c = _cliente(_ok(reqs))
    ruta = await c.subir(TENANT, bytes_)
    assert _patron(ext).match(ruta)
    assert len(reqs) == 1  # subir no borra nada
    subida = reqs[0]
    assert subida.method == "POST"
    assert subida.url.path == f"/storage/v1/object/logos/{ruta}"
    assert subida.headers["content-type"] == mime
    assert subida.headers["authorization"] == f"Bearer {CLAVE}"
    assert "x-upsert" not in subida.headers  # nunca pisa un objeto existente
    assert subida.content == bytes_


async def test_cada_subida_tiene_su_propio_nombre():
    c = _cliente(_ok([]))
    rutas = {await c.subir(TENANT, PNG) for _ in range(20)}
    assert len(rutas) == 20


async def test_extension_enganosa_se_decide_por_los_bytes():
    reqs: list[httpx.Request] = []
    c = _cliente(_ok(reqs))
    with pytest.raises(TipoNoPermitido):
        await c.subir(TENANT, SVG, content_type_declarado="image/png", nombre_original="logo.png")
    assert reqs == []
    ruta = await c.subir(TENANT, PNG, content_type_declarado="image/svg+xml", nombre_original="x.svg")
    assert ruta.endswith(".png")


@pytest.mark.parametrize(
    "contenido", [SVG, b"GIF89a" + b"\x00" * 10, b"%PDF-1.4", b"", b"RIFF\x00\x00\x00\x00WAVEfmt ", b"\x89PNG"]
)
async def test_rechaza_lo_que_no_es_png_jpg_webp(contenido):
    reqs: list[httpx.Request] = []
    with pytest.raises(TipoNoPermitido):
        await _cliente(_ok(reqs)).subir(TENANT, contenido)
    assert reqs == []


async def test_rechaza_mas_de_un_mega():
    reqs: list[httpx.Request] = []
    c = _cliente(_ok(reqs))
    with pytest.raises(ArchivoDemasiadoGrande):
        await c.subir(TENANT, PNG + b"\x00" * (1024 * 1024))
    assert reqs == []
    await c.subir(TENANT, PNG + b"\x00" * (1024 * 1024 - len(PNG)))  # justo 1 MB


@pytest.mark.parametrize(
    "malo", ["../otro", "..", "a/b", "a\b", "", " ", "11111111-2222-3333-4444-555555555555/../x", "%2e%2e", None, 7]
)
async def test_ningun_tenant_escapa_de_su_carpeta(malo):
    reqs: list[httpx.Request] = []
    c = _cliente(_ok(reqs))
    with pytest.raises(ErrorAlmacenamiento):
        await c.subir(malo, PNG)
    with pytest.raises(ErrorAlmacenamiento):
        await c.borrar_ruta(malo, f"{TENANT}/logo-{ID}.png")
    with pytest.raises(ErrorAlmacenamiento):
        await c.borrar_anteriores(malo, None)
    with pytest.raises(ErrorAlmacenamiento):
        ruta_logo(malo, "png", ID)
    assert reqs == []


def test_la_ruta_depende_del_tenant_la_extension_y_el_id():
    assert ruta_logo(str(TENANT).upper(), "png", ID) == f"{TENANT}/logo-{ID}.png"
    with pytest.raises(ErrorAlmacenamiento):
        ruta_logo(TENANT, "svg", ID)
    for id_malo in ("", "a", ID.upper(), ID + "0", "../" + ID[3:], None):
        with pytest.raises(ErrorAlmacenamiento):
            ruta_logo(TENANT, "png", id_malo)


@pytest.mark.parametrize(
    "ruta",
    [
        f"{uuid.uuid4()}/logo-{ID}.png",  # de otro tenant
        f"{TENANT}/logo.png",
        f"{TENANT}/logo-{ID}.svg",
        f"{TENANT}/../logo-{ID}.png",
        f"{TENANT}/sub/logo-{ID}.png",
        f"{TENANT}/logo-{ID}.png/",
        "",
        None,
    ],
)
async def test_borrar_ruta_solo_acepta_logos_del_propio_tenant(ruta):
    reqs: list[httpx.Request] = []
    with pytest.raises(ErrorAlmacenamiento):
        await _cliente(_ok(reqs)).borrar_ruta(TENANT, ruta)
    assert reqs == []


async def test_borrar_ruta_borra_solo_ese_objeto():
    reqs: list[httpx.Request] = []
    await _cliente(_ok(reqs)).borrar_ruta(TENANT, f"{TENANT}/logo-{ID}.jpg")
    assert reqs[0].method == "DELETE"
    assert json.loads(reqs[0].content)["prefixes"] == [f"{TENANT}/logo-{ID}.jpg"]


def _listado(objetos: dict[str, float], reqs):
    """Storage mínimo: lista la carpeta (con `created_at`) y registra los borrados."""

    def handler(req: httpx.Request) -> httpx.Response:
        reqs.append(req)
        if req.url.path.startswith("/storage/v1/object/list/"):
            return httpx.Response(
                200,
                json=[
                    {"name": n, "created_at": datetime.fromtimestamp(t, timezone.utc).isoformat()}  # noqa: UP017
                    for n, t in objetos.items()
                ],
            )
        return httpx.Response(200, json=[])

    return handler


def _borrados(reqs):
    return [json.loads(r.content)["prefixes"] for r in reqs if r.method == "DELETE"]


async def test_borrar_anteriores_borra_lo_viejo_que_no_es_el_vigente_y_respeta_la_gracia():
    ahora = time.time()
    vigente, viejo, reciente = (f"logo-{c * 32}.png" for c in "abc")
    reqs: list[httpx.Request] = []
    objetos = {
        vigente: ahora - 7200,
        viejo: ahora - 7200,
        reciente: ahora - 5,  # una subida en curso
        "otro.png": ahora - 7200,  # no es un logo
        f"logo-{'d' * 32}.svg": ahora - 7200,  # extensión que no se maneja
    }
    c = _cliente(_listado(objetos, reqs))
    borradas = await c.borrar_anteriores(TENANT, f"{TENANT}/{vigente}")
    assert borradas == [f"{TENANT}/{viejo}"]
    assert _borrados(reqs) == [[f"{TENANT}/{viejo}"]]
    assert json.loads(reqs[0].content)["prefix"] == str(TENANT)


async def test_borrar_anteriores_sin_vigente_borra_todo_lo_viejo_y_no_pide_borrar_si_no_hay_nada():
    ahora = time.time()
    reqs: list[httpx.Request] = []
    c = _cliente(_listado({f"logo-{'a' * 32}.png": ahora - 7200}, reqs))
    assert await c.borrar_anteriores(TENANT, None) == [f"{TENANT}/logo-{'a' * 32}.png"]
    reqs.clear()
    c = _cliente(_listado({f"logo-{'a' * 32}.png": ahora}, reqs))
    assert await c.borrar_anteriores(TENANT, None) == []
    assert _borrados(reqs) == []


async def test_un_objeto_sin_fecha_legible_se_conserva():
    reqs: list[httpx.Request] = []

    def handler(req):
        reqs.append(req)
        if "/list/" in req.url.path:
            return httpx.Response(200, json=[{"name": f"logo-{'a' * 32}.png", "created_at": "no es una fecha"}])
        return httpx.Response(200, json=[])

    assert await _cliente(handler).borrar_anteriores(TENANT, None) == []
    assert _borrados(reqs) == []


async def test_un_listado_con_forma_rara_es_error_propio():
    c = _cliente(lambda req: httpx.Response(200, json={"otra": 1}))
    with pytest.raises(AlmacenamientoNoDisponible):
        await c.borrar_anteriores(TENANT, None)


async def test_url_publica():
    c = _cliente(_ok([]), publico=True)
    assert (
        await c.url_publica_o_firmada(TENANT, f"{TENANT}/logo-{ID}.png")
        == f"https://proy.supabase.co/storage/v1/object/public/logos/{TENANT}/logo-{ID}.png"
    )


async def test_url_firmada():
    reqs: list[httpx.Request] = []
    c = _cliente(_ok(reqs))
    url = await c.url_publica_o_firmada(TENANT, f"{TENANT}/logo-{ID}.webp", expira_seg=600)
    assert url == "https://proy.supabase.co/storage/v1/object/sign/logos/a?token=t"
    assert reqs[0].url.path == f"/storage/v1/object/sign/logos/{TENANT}/logo-{ID}.webp"
    assert json.loads(reqs[0].content) == {"expiresIn": 600}


async def test_la_url_solo_se_arma_para_logos_del_propio_tenant():
    c = _cliente(_ok([]), publico=True)
    with pytest.raises(ErrorAlmacenamiento):
        await c.url_publica_o_firmada(TENANT, f"{uuid.uuid4()}/logo-{ID}.png")


async def test_falla_del_proveedor_no_refleja_su_respuesta_ni_la_clave(caplog):
    def handler(req):
        return httpx.Response(500, text=f"detalle interno {CLAVE} bucket secreto")

    with pytest.raises(AlmacenamientoNoDisponible) as e:
        await _cliente(handler).subir(TENANT, PNG)
    assert not isinstance(e.value, AlmacenamientoSinRespuesta)  # el proveedor respondió: no se aplicó
    assert "interno" not in str(e.value) and CLAVE not in str(e.value)
    assert CLAVE not in caplog.text and "interno" not in caplog.text


async def test_timeout_y_error_de_red_son_sin_respuesta_y_traen_la_ruta_de_la_subida():
    def handler(req):
        raise httpx.ConnectTimeout("lento", request=req)

    with pytest.raises(AlmacenamientoSinRespuesta) as e:
        await _cliente(handler).subir(TENANT, PNG)
    assert e.value.__cause__ is None
    assert _patron("png").match(e.value.ruta)
    with pytest.raises(AlmacenamientoSinRespuesta) as e:
        await _cliente(handler).borrar_ruta(TENANT, f"{TENANT}/logo-{ID}.png")
    assert e.value.ruta is None


async def test_url_firmada_con_respuesta_rara_es_error_propio():
    c = _cliente(lambda req: httpx.Response(200, json={"otra": 1}))
    with pytest.raises(AlmacenamientoNoDisponible):
        await c.url_publica_o_firmada(TENANT, f"{TENANT}/logo-{ID}.png")


def test_el_cliente_se_crea_con_timeout_explicito_y_no_muestra_la_clave():
    c = ClienteAlmacenamiento("https://proy.supabase.co", CLAVE, timeout=3.0)
    assert c._http.timeout.read == 3.0
    assert CLAVE not in repr(c)


async def test_la_clave_no_aparece_en_logs_ni_mensaje_de_un_pedido_fallido(caplog):
    def handler(req):
        raise httpx.ConnectError(f"fallo con {CLAVE}", request=req)

    caplog.set_level("DEBUG")
    with pytest.raises(AlmacenamientoNoDisponible) as e:
        await _cliente(handler).subir(TENANT, PNG)
    assert CLAVE not in str(e.value) and CLAVE not in repr(e.value)
    assert CLAVE not in caplog.text


@pytest.mark.parametrize(
    ("url", "clave", "bucket", "timeout"),
    [
        ("", CLAVE, "logos", 5.0),
        ("http://proy.supabase.co", CLAVE, "logos", 5.0),
        ("https://proy.supabase.co", "", "logos", 5.0),
        ("https://proy.supabase.co", CLAVE, "a/b", 5.0),
        ("https://proy.supabase.co", CLAVE, "", 5.0),
        ("https://proy.supabase.co", CLAVE, "logos", 0),
    ],
)
def test_configuracion_invalida_falla_al_arrancar(url, clave, bucket, timeout):
    with pytest.raises(ConfiguracionAlmacenamientoInvalida):
        ClienteAlmacenamiento(url, clave, bucket=bucket, timeout=timeout)


def test_http_solo_hacia_loopback():
    ClienteAlmacenamiento("http://127.0.0.1:54321", CLAVE)


async def test_plazo_total_corta_una_respuesta_que_demora():
    async def lento(req):
        await asyncio.sleep(2)
        return httpx.Response(200, json={})

    http = httpx.AsyncClient(transport=httpx.MockTransport(lento), timeout=5.0)
    c = ClienteAlmacenamiento("https://proy.supabase.co", CLAVE, http=http, timeout=0.05)
    with pytest.raises(AlmacenamientoSinRespuesta) as e:
        await c.subir(TENANT, PNG)
    assert _patron("png").match(e.value.ruta)


async def test_no_reintenta_ante_fallos():
    llamadas = []

    def handler(req):
        llamadas.append(1)
        return httpx.Response(503)

    with pytest.raises(AlmacenamientoNoDisponible):
        await _cliente(handler).subir(TENANT, PNG)
    assert len(llamadas) == 1


@pytest.mark.parametrize("estado", [301, 302, 307, 308])
async def test_una_redireccion_no_es_exito_al_subir_ni_al_borrar(estado):
    def handler(req):
        return httpx.Response(estado, headers={"location": "https://otro.example/x"})

    with pytest.raises(AlmacenamientoNoDisponible):
        await _cliente(handler).subir(TENANT, PNG)
    with pytest.raises(AlmacenamientoNoDisponible):
        await _cliente(handler).borrar_ruta(TENANT, f"{TENANT}/logo-{ID}.png")


@pytest.mark.parametrize("estado", [400, 404, 500, 503])
async def test_falla_al_firmar_la_url_es_error_propio(estado):
    c = _cliente(lambda req: httpx.Response(estado, text="detalle interno"))
    with pytest.raises(AlmacenamientoNoDisponible) as e:
        await c.url_publica_o_firmada(TENANT, f"{TENANT}/logo-{ID}.png")
    assert "interno" not in str(e.value)


@pytest.mark.parametrize("url", ["https://host:abc", "http://[abc", "https://host:99999999", "ftp://host"])
def test_url_malformada_falla_al_arrancar(url):
    with pytest.raises(ConfiguracionAlmacenamientoInvalida):
        ClienteAlmacenamiento(url, CLAVE)


async def test_url_invalida_en_httpx_es_error_propio():
    def handler(req):
        raise httpx.InvalidURL("url rota")

    with pytest.raises(AlmacenamientoNoDisponible):
        await _cliente(handler).borrar_ruta(TENANT, f"{TENANT}/logo-{ID}.png")


async def test_un_404_al_borrar_es_un_error_propio_distinguible_y_sigue_siendo_no_disponible():
    def handler(req):
        return httpx.Response(404, json={"message": "detalle interno"})

    with pytest.raises(ObjetoNoEncontrado) as e:
        await _cliente(handler).borrar_ruta(TENANT, f"{TENANT}/logo-{ID}.png")
    assert isinstance(e.value, AlmacenamientoNoDisponible)  # los llamadores existentes lo siguen tratando igual
    assert "interno" not in str(e.value)


@pytest.mark.parametrize("estado", [400, 401, 403, 500, 502, 503])
async def test_otros_estados_de_error_al_borrar_no_son_objeto_no_encontrado(estado):
    with pytest.raises(AlmacenamientoNoDisponible) as e:
        await _cliente(lambda req: httpx.Response(estado)).borrar_ruta(TENANT, f"{TENANT}/logo-{ID}.png")
    assert not isinstance(e.value, ObjetoNoEncontrado)
