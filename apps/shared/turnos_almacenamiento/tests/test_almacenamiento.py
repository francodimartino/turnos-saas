import asyncio
import json
import uuid

import httpx
import pytest

from turnos_almacenamiento import (
    AlmacenamientoNoDisponible,
    ArchivoDemasiadoGrande,
    ClienteAlmacenamiento,
    ConfiguracionAlmacenamientoInvalida,
    ErrorAlmacenamiento,
    TipoNoPermitido,
    ruta_logo,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 20
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 20
SVG = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"></svg>'
TENANT = uuid.UUID("11111111-2222-3333-4444-555555555555")
CLAVE = "clave-de-servicio-secreta"


def _cliente(handler, **kw):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    return ClienteAlmacenamiento("https://proy.supabase.co", CLAVE, http=http, **kw)


def _ok(peticiones):
    def handler(req: httpx.Request) -> httpx.Response:
        peticiones.append(req)
        return httpx.Response(200, json={"Key": "x", "signedURL": "/object/sign/logos/a?token=t"})

    return handler


@pytest.mark.parametrize(
    ("bytes_", "ext", "mime"), [(PNG, "png", "image/png"), (JPG, "jpg", "image/jpeg"), (WEBP, "webp", "image/webp")]
)
async def test_sube_el_formato_detectado_por_bytes(bytes_, ext, mime):
    reqs: list[httpx.Request] = []
    c = _cliente(_ok(reqs))
    ruta = await c.subir(TENANT, bytes_)
    assert ruta == f"{TENANT}/logo.{ext}"
    subida = reqs[0]
    assert subida.method == "POST"
    assert subida.url.path == f"/storage/v1/object/logos/{TENANT}/logo.{ext}"
    assert subida.headers["content-type"] == mime
    assert subida.headers["authorization"] == f"Bearer {CLAVE}"
    assert subida.headers["x-upsert"] == "true"
    assert subida.content == bytes_


async def test_al_subir_borra_las_otras_extensiones():
    reqs: list[httpx.Request] = []
    await _cliente(_ok(reqs)).subir(TENANT, PNG)
    borrado = reqs[1]
    assert borrado.method == "DELETE"
    assert json.loads(borrado.content)["prefixes"] == [f"{TENANT}/logo.jpg", f"{TENANT}/logo.webp"]


async def test_extension_enganosa_se_decide_por_los_bytes():
    reqs: list[httpx.Request] = []
    # Un SVG "disfrazado" de png no pasa; un PNG real sube como png aunque se lo llame .svg.
    c = _cliente(_ok(reqs))
    with pytest.raises(TipoNoPermitido):
        await c.subir(TENANT, SVG, content_type_declarado="image/png", nombre_original="logo.png")
    assert reqs == []
    ruta = await c.subir(TENANT, PNG, content_type_declarado="image/svg+xml", nombre_original="x.svg")
    assert ruta.endswith("/logo.png")


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
        await c.borrar(malo)
    with pytest.raises(ErrorAlmacenamiento):
        ruta_logo(malo, "png")
    assert reqs == []


def test_la_ruta_solo_depende_del_tenant_y_de_la_extension():
    assert ruta_logo(str(TENANT).upper(), "png") == f"{TENANT}/logo.png"
    with pytest.raises(ErrorAlmacenamiento):
        ruta_logo(TENANT, "svg")


async def test_borrar_quita_todas_las_variantes():
    reqs: list[httpx.Request] = []
    await _cliente(_ok(reqs)).borrar(TENANT)
    assert json.loads(reqs[0].content)["prefixes"] == [f"{TENANT}/logo.{e}" for e in ("png", "jpg", "webp")]


async def test_url_publica():
    c = _cliente(_ok([]), publico=True)
    assert (
        await c.url_publica_o_firmada(TENANT, "png")
        == f"https://proy.supabase.co/storage/v1/object/public/logos/{TENANT}/logo.png"
    )


async def test_url_firmada():
    reqs: list[httpx.Request] = []
    c = _cliente(_ok(reqs))
    url = await c.url_publica_o_firmada(TENANT, "webp", expira_seg=600)
    assert url == "https://proy.supabase.co/storage/v1/object/sign/logos/a?token=t"
    assert reqs[0].url.path == f"/storage/v1/object/sign/logos/{TENANT}/logo.webp"
    assert json.loads(reqs[0].content) == {"expiresIn": 600}


async def test_falla_del_proveedor_no_refleja_su_respuesta_ni_la_clave(caplog):
    def handler(req):
        return httpx.Response(500, text=f"detalle interno {CLAVE} bucket secreto")

    with pytest.raises(AlmacenamientoNoDisponible) as e:
        await _cliente(handler).subir(TENANT, PNG)
    assert "interno" not in str(e.value) and CLAVE not in str(e.value)
    assert CLAVE not in caplog.text and "interno" not in caplog.text


async def test_timeout_y_error_de_red_son_error_propio():
    def handler(req):
        raise httpx.ConnectTimeout("lento", request=req)

    with pytest.raises(AlmacenamientoNoDisponible) as e:
        await _cliente(handler).borrar(TENANT)
    assert e.value.__cause__ is None


async def test_url_firmada_con_respuesta_rara_es_error_propio():
    c = _cliente(lambda req: httpx.Response(200, json={"otra": 1}))
    with pytest.raises(AlmacenamientoNoDisponible):
        await c.url_publica_o_firmada(TENANT, "png")


def test_el_cliente_se_crea_con_timeout_explicito_y_no_muestra_la_clave():
    c = ClienteAlmacenamiento("https://proy.supabase.co", CLAVE, timeout=3.0)
    assert c._http.timeout.read == 3.0
    assert CLAVE not in repr(c) and CLAVE not in str(vars(c).get("_http", ""))


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
    with pytest.raises(AlmacenamientoNoDisponible):
        await c.subir(TENANT, PNG)


async def test_no_reintenta_ante_fallos():
    llamadas = []

    def handler(req):
        llamadas.append(1)
        return httpx.Response(503)

    with pytest.raises(AlmacenamientoNoDisponible):
        await _cliente(handler).subir(TENANT, PNG)
    assert len(llamadas) == 1
