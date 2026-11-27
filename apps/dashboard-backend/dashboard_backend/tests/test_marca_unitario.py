"""Marca del local sin base real: logo (Storage simulado), colores y plazo de cancelación.

Cubre el caso feliz, tipo inválido, tamaño, Storage caído, 403, aislamiento entre tenants, auditoría, el orden
(Storage fuera de la transacción, base solo después) y que la clave de Storage no salga a logs ni a respuestas.
"""

import asyncio
import json
import logging
import uuid

import httpx
import pytest
from turnos_almacenamiento import ClienteAlmacenamiento

from dashboard_backend.tests.soporte import (
    ORIGEN,
    ConnFalsa,
    PoolFalso,
    config_prueba,
    http,
    token,
)
from dashboard_backend.tests.soporte import app_de_prueba as _app_de_prueba

TENANT = uuid.uuid4()
OTRO_TENANT = uuid.uuid4()
USUARIO = uuid.uuid4()
CLAVE = "clave-de-servicio-ultra-secreta"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 64
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
MEGA = 1024 * 1024


class ConnMarca(ConnFalsa):
    """ConnFalsa con la fila de settings del tenant y el registro del orden de los eventos."""

    def __init__(self, eventos):
        super().__init__()
        self.eventos = eventos
        self.settings: dict = {}
        self.fallo_escritura: Exception | None = None
        self.estado_staff["tiene"] = True

    async def execute(self, sql, *args):
        self.eventos.append(sql.split()[0].lower())
        await super().execute(sql, *args)

    async def fetchrow(self, sql, *args):
        previo = await super().fetchrow(sql, *args)
        if previo is not None:
            return previo
        if sql.lstrip().lower().startswith("update public.tenants"):
            if self.fallo_escritura is not None:
                raise self.fallo_escritura
            self.settings = json.loads(args[1])
            self.eventos.append("update_settings")
        if "public.tenants" in sql:
            return {"settings": self.settings}
        return None

    async def fetchval(self, sql, *args):
        if "write_audit_log" in sql:
            self.eventos.append("auditoria")
        return await super().fetchval(sql, *args)

    def actualizaciones(self):
        return [(s, a) for s, a in self.ejecutado if s.lstrip().lower().startswith("update public.tenants")]


class Entorno:
    def __init__(self, handler=None, *, con_almacenamiento=True):
        self.eventos: list[str] = []
        self.peticiones: list[httpx.Request] = []
        self.handler = handler or (lambda req: httpx.Response(200, json={"Key": "x"}))
        pool = PoolFalso()
        pool.conn = ConnMarca(self.eventos)
        self.conn = pool.conn
        almacenamiento = None
        if con_almacenamiento:
            transporte = httpx.MockTransport(self._atender)
            almacenamiento = ClienteAlmacenamiento(
                "https://proy.supabase.co", CLAVE, http=httpx.AsyncClient(transport=transporte)
            )
        self.app, _, _ = _app_de_prueba(pool=pool, almacenamiento=almacenamiento)

    def _atender(self, req: httpx.Request) -> httpx.Response:
        self.peticiones.append(req)
        self.eventos.append("storage_" + req.method.lower())
        return self.handler(req)

    async def pedir(self, metodo, ruta, *, contenido=None, cuerpo=None, tenant=TENANT, headers=None, **kw):
        h = {"Origin": ORIGEN, "Cookie": f"dash_acceso={token(user_id=USUARIO, tenant_id=tenant, **kw)}"}
        h.update(headers or {})
        return await http(self.app).request(metodo, ruta, content=contenido, json=cuerpo, headers=h)


def _app_con(handler=None, **kw):
    return Entorno(handler, **kw)


def _fallo_storage(req):
    return httpx.Response(500, json={"message": f"detalle interno del proveedor {CLAVE}"})


# --- subir ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("bytes_", "ext"), [(PNG, "png"), (JPG, "jpg"), (WEBP, "webp")])
async def test_subir_logo_guarda_en_storage_y_la_ruta_en_settings(bytes_, ext):
    e = _app_con()
    r = await e.pedir("POST", "/local/logo", contenido=bytes_)
    assert r.status_code == 200, r.text
    ruta = f"{TENANT}/logo.{ext}"
    assert r.json() == {"settings": {"logo_ruta": ruta}}
    assert e.conn.settings == {"logo_ruta": ruta}
    subida = e.peticiones[0]
    assert subida.method == "POST" and subida.url.path.endswith(f"/object/logos/{TENANT}/logo.{ext}")
    assert subida.content == bytes_


async def test_la_subida_a_storage_ocurre_fuera_de_transaccion_y_la_base_se_toca_despues():
    e = _app_con()
    assert (await e.pedir("POST", "/local/logo", contenido=PNG)).status_code == 200
    ev = e.eventos
    primera_subida = ev.index("storage_post")
    # Todo BEGIN anterior a la subida ya tiene su COMMIT antes de ella: no hay una transacción abierta durante Storage.
    previos = ev[:primera_subida]
    assert previos.count("begin") == previos.count("commit")
    assert ev.index("update_settings") > primera_subida
    assert ev.index("auditoria") > ev.index("update_settings")
    # La limpieza de los otros formatos es posterior al COMMIT que guardó la ruta, y fuera de transacción.
    limpieza = ev.index("storage_delete")
    assert limpieza > ev.index("update_settings")
    assert ev[:limpieza].count("begin") == ev[:limpieza].count("commit")
    assert ev[-1] == "storage_delete"
    # La subida no borra nada por su cuenta: el único DELETE es la limpieza tras guardar.
    assert ev.count("storage_delete") == 1
    prefijos = json.loads(e.peticiones[-1].content)["prefixes"]
    assert f"{TENANT}/logo.png" not in prefijos


async def test_subir_logo_deja_audit_log_con_las_claves_y_sin_valores():
    e = _app_con()
    await e.pedir("POST", "/local/logo", contenido=PNG)
    assert len(e.conn.auditoria) == 1
    args = e.conn.auditoria[0]
    assert "settings_updated" in args and "tenants" in args
    assert json.loads(args[3]) == {"claves": ["logo_ruta"]}


@pytest.mark.parametrize(
    "contenido", [SVG, b"", b"texto cualquiera", b"GIF89a" + b"\x00" * 20, b"%PDF-1.4 " + b"\x00" * 20]
)
async def test_un_archivo_que_no_es_png_jpg_o_webp_da_422_sin_tocar_storage_ni_la_base(contenido):
    e = _app_con()
    r = await e.pedir("POST", "/local/logo", contenido=contenido, headers={"Content-Type": "image/png"})
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["codigo"] == "logo_tipo_invalido"
    assert e.peticiones == []
    assert e.conn.actualizaciones() == []


async def test_el_content_type_y_el_nombre_falsos_no_engañan():
    e = _app_con()
    r = await e.pedir(
        "POST",
        "/local/logo",
        contenido=SVG,
        headers={"Content-Type": "image/png", "Content-Disposition": 'attachment; filename="logo.png"'},
    )
    assert r.status_code == 422
    assert e.peticiones == []


async def test_un_png_aunque_declare_otro_tipo_se_acepta_por_sus_bytes():
    e = _app_con()
    r = await e.pedir("POST", "/local/logo", contenido=PNG, headers={"Content-Type": "image/svg+xml"})
    assert r.status_code == 200
    assert r.json()["settings"]["logo_ruta"].endswith("/logo.png")


async def test_un_logo_de_exactamente_1_mb_se_acepta():
    e = _app_con()
    r = await e.pedir("POST", "/local/logo", contenido=PNG + b"\x00" * (MEGA - len(PNG)))
    assert r.status_code == 200, r.text


async def test_un_logo_de_1_mb_mas_un_byte_da_413_sin_tocar_storage_ni_la_base():
    e = _app_con()
    r = await e.pedir("POST", "/local/logo", contenido=PNG + b"\x00" * (MEGA + 1 - len(PNG)))
    assert r.status_code == 413, r.text
    assert e.peticiones == []
    assert e.conn.actualizaciones() == []


async def test_el_cliente_de_storage_tambien_rechaza_lo_demasiado_grande_con_4xx(monkeypatch):
    # Defensa en profundidad: si el límite de body del servidor fuera mayor, el cliente de Storage lo frena igual.
    from turnos_almacenamiento import cliente as modulo

    monkeypatch.setattr(modulo, "TAMANO_MAXIMO_BYTES", 100)
    e = _app_con()
    r = await e.pedir("POST", "/local/logo", contenido=PNG + b"\x00" * 200)
    assert r.status_code == 413
    assert r.json()["detail"]["codigo"] == "logo_demasiado_grande"
    assert e.peticiones == []


# --- Storage caído -------------------------------------------------------------------------------------------------


async def test_storage_con_error_da_503_y_la_base_no_queda_con_una_ruta_inexistente(caplog):
    e = _app_con(_fallo_storage)
    e.conn.settings = {"color_principal": "#112233"}
    with caplog.at_level(logging.DEBUG):
        r = await e.pedir("POST", "/local/logo", contenido=PNG)
    assert r.status_code == 503, r.text
    assert r.json()["detail"]["codigo"] == "almacenamiento_no_disponible"
    assert e.conn.actualizaciones() == []
    assert e.conn.settings == {"color_principal": "#112233"}
    assert "update_settings" not in e.eventos
    # Ni la clave ni el detalle del proveedor salen en la respuesta ni en los logs.
    assert CLAVE not in r.text and "detalle interno" not in r.text
    assert CLAVE not in caplog.text and "detalle interno" not in caplog.text


async def test_storage_sin_red_da_503():
    def sin_red(req):
        raise httpx.ConnectError("sin red", request=req)

    e = _app_con(sin_red)
    r = await e.pedir("POST", "/local/logo", contenido=PNG)
    assert r.status_code == 503
    assert e.conn.actualizaciones() == []
    assert r.headers["Retry-After"] == "5"


async def test_storage_que_no_responde_a_tiempo_da_503():
    import asyncio

    async def lento(req):
        await asyncio.sleep(5)
        return httpx.Response(200)

    e = _app_con()
    transporte = httpx.MockTransport(lento)
    pool = PoolFalso()
    pool.conn = ConnMarca([])
    almacenamiento = ClienteAlmacenamiento(
        "https://proy.supabase.co", CLAVE, timeout=0.05, http=httpx.AsyncClient(transport=transporte)
    )
    e.app, _, _ = _app_de_prueba(pool=pool, almacenamiento=almacenamiento)
    r = await e.pedir("POST", "/local/logo", contenido=PNG)
    assert r.status_code == 503
    assert pool.conn.actualizaciones() == []


async def test_sin_cliente_de_storage_subir_y_borrar_dan_503_no_500():
    e = _app_con(con_almacenamiento=False)
    assert (await e.pedir("POST", "/local/logo", contenido=PNG)).status_code == 503
    assert (await e.pedir("DELETE", "/local/logo")).status_code == 503
    assert e.conn.actualizaciones() == []


# --- borrar --------------------------------------------------------------------------------------------------------


async def test_borrar_logo_saca_el_objeto_y_la_clave_y_audita():
    e = _app_con()
    e.conn.settings = {"logo_ruta": f"{TENANT}/logo.png", "color_principal": "#112233"}
    r = await e.pedir("DELETE", "/local/logo")
    assert r.status_code == 200, r.text
    assert r.json() == {"settings": {"color_principal": "#112233"}}
    borrado = e.peticiones[-1]
    assert borrado.method == "DELETE"
    prefijos = json.loads(borrado.content)["prefixes"]
    assert prefijos == [f"{TENANT}/logo.png", f"{TENANT}/logo.jpg", f"{TENANT}/logo.webp"]
    assert json.loads(e.conn.auditoria[0][3]) == {"claves": ["logo_ruta"]}
    assert e.eventos.index("storage_delete") > e.eventos.index("update_settings")
    assert e.eventos[: e.eventos.index("storage_delete")].count("begin") == e.eventos[
        : e.eventos.index("storage_delete")
    ].count("commit")


async def test_si_storage_falla_al_borrar_la_clave_ya_se_quito_y_el_huerfano_se_registra(caplog):
    e = _app_con(_fallo_storage)
    e.conn.settings = {"logo_ruta": f"{TENANT}/logo.png"}
    with caplog.at_level(logging.WARNING):
        r = await e.pedir("DELETE", "/local/logo")
    assert r.status_code == 200, r.text
    assert "logo_ruta" not in e.conn.settings  # nunca una ruta colgada
    assert any(getattr(x, "motivo", None) == "logo_huerfano" for x in caplog.records)
    assert CLAVE not in r.text and CLAVE not in caplog.text


async def test_si_la_base_falla_al_borrar_no_se_toca_storage_y_la_ruta_sigue_valida():
    e = _app_con()
    e.conn.settings = {"logo_ruta": f"{TENANT}/logo.png"}
    e.conn.fallo_escritura = TimeoutError()
    r = await e.pedir("DELETE", "/local/logo")
    assert r.status_code == 503
    assert e.peticiones == []  # el objeto sigue existiendo y la ruta también
    assert e.conn.settings == {"logo_ruta": f"{TENANT}/logo.png"}


async def test_si_la_base_falla_tras_subir_el_logo_anterior_sigue_intacto_y_el_huerfano_se_registra(caplog):
    e = _app_con()
    e.conn.settings = {"logo_ruta": f"{TENANT}/logo.png"}  # logo anterior en otro formato
    e.conn.fallo_escritura = TimeoutError()
    with caplog.at_level(logging.WARNING):
        r = await e.pedir("POST", "/local/logo", contenido=JPG)
    assert r.status_code == 503, r.text
    assert e.conn.settings == {"logo_ruta": f"{TENANT}/logo.png"}  # apunta a algo que existe
    # No se borró nada de Storage (el png anterior sigue ahí): solo hubo la subida del jpg.
    assert [p.method for p in e.peticiones] == ["POST"]
    assert any(getattr(x, "motivo", None) == "logo_huerfano" for x in caplog.records)


async def test_si_falla_la_limpieza_de_otros_formatos_la_subida_igual_responde_200(caplog):
    def handler(req):
        return httpx.Response(500) if req.method == "DELETE" else httpx.Response(200, json={})

    e = _app_con(handler)
    with caplog.at_level(logging.WARNING):
        r = await e.pedir("POST", "/local/logo", contenido=JPG)
    assert r.status_code == 200
    assert e.conn.settings == {"logo_ruta": f"{TENANT}/logo.jpg"}
    assert any(getattr(x, "motivo", None) == "logo_huerfano" for x in caplog.records)


# --- operaciones cruzadas del mismo negocio --------------------------------------------------------------------------


async def test_dos_subidas_cruzadas_de_distinto_formato_la_ultima_gana_y_la_ruta_apunta_a_algo_que_existe():
    a_en_storage, liberar = asyncio.Event(), asyncio.Event()

    async def handler(req):
        if req.method == "POST" and req.url.path.endswith("logo.png"):
            a_en_storage.set()
            await liberar.wait()  # la subida del png queda a mitad de camino
        elif req.method == "POST":
            liberar.set()
        return httpx.Response(200, json={})

    e = _app_con(handler)
    tarea_a = asyncio.create_task(e.pedir("POST", "/local/logo", contenido=PNG))
    await a_en_storage.wait()
    r_b = await e.pedir("POST", "/local/logo", contenido=JPG)
    r_a = await tarea_a
    assert r_b.status_code == 200, r_b.text
    assert r_a.status_code == 409 and r_a.json()["detail"]["codigo"] == "logo_reemplazado"
    assert e.conn.settings == {"logo_ruta": f"{TENANT}/logo.jpg"}
    # Nadie borró el objeto al que apunta la ruta.
    for p in e.peticiones:
        if p.method == "DELETE":
            assert f"{TENANT}/logo.jpg" not in json.loads(p.content)["prefixes"]


async def test_una_subida_cruzada_con_un_borrado_no_deja_la_ruta_apuntando_a_nada():
    a_en_storage, liberar = asyncio.Event(), asyncio.Event()

    async def handler(req):
        if req.method == "POST":
            a_en_storage.set()
            await liberar.wait()
        else:
            liberar.set()
        return httpx.Response(200, json={})

    e = _app_con(handler)
    e.conn.settings = {"color_principal": "#112233"}
    tarea_a = asyncio.create_task(e.pedir("POST", "/local/logo", contenido=PNG))
    await a_en_storage.wait()
    r_b = await e.pedir("DELETE", "/local/logo")
    r_a = await tarea_a
    assert r_b.status_code == 200
    assert r_a.status_code == 409
    assert "logo_ruta" not in e.conn.settings


async def test_el_guardado_de_la_ruta_toma_el_lock_por_tenant_dentro_de_la_transaccion():
    e = _app_con()
    await e.pedir("POST", "/local/logo", contenido=PNG)
    lock = [a for s_, a in e.conn.ejecutado if "pg_advisory_xact_lock" in s_]
    assert lock == [(f"logo:{TENANT}",)]
    ev = e.eventos
    assert ev.index("update_settings") > max(i for i, x in enumerate(ev) if x == "select")


# --- tope de settings ----------------------------------------------------------------------------------------------


async def test_settings_demasiado_grandes_dan_422_en_apariencia_y_subida_sin_tocar_storage():
    e = _app_con()
    e.conn.settings = {"relleno": "x" * 19_960}
    r = await e.pedir(
        "PATCH", "/local/apariencia", cuerpo={"color_principal": "#AABBCC", "color_secundario": "#112233"}
    )
    assert r.status_code == 422 and r.json()["detail"]["codigo"] == "settings_demasiado_grandes"
    r = await e.pedir("POST", "/local/logo", contenido=PNG)
    assert r.status_code == 422 and r.json()["detail"]["codigo"] == "settings_demasiado_grandes"
    assert e.peticiones == []  # se rechazó antes de subir nada
    assert e.conn.actualizaciones() == []


async def test_borrar_traduce_el_tope_de_settings_a_422(monkeypatch):
    from dashboard_backend import settings_local

    async def excedido(*a, **k):
        raise settings_local.ErrorSettingsDemasiadoGrandes

    monkeypatch.setattr(settings_local, "modificar_settings", excedido)
    e = _app_con()
    r = await e.pedir("DELETE", "/local/logo")
    assert r.status_code == 422 and r.json()["detail"]["codigo"] == "settings_demasiado_grandes"


# --- permisos ------------------------------------------------------------------------------------------------------

ESCRITURAS = [
    ("POST", "/local/logo", {"contenido": PNG}),
    ("DELETE", "/local/logo", {}),
    ("PATCH", "/local/apariencia", {"cuerpo": {"color_principal": "#112233"}}),
]


@pytest.mark.parametrize(("metodo", "ruta", "kw"), ESCRITURAS)
async def test_sin_el_permiso_de_configurar_el_local_da_403_y_no_toca_nada(metodo, ruta, kw):
    e = _app_con()
    e.conn.estado_staff["tiene"] = False
    r = await e.pedir(metodo, ruta, **kw)
    assert r.status_code == 403
    assert e.peticiones == []
    assert e.conn.actualizaciones() == [] and e.conn.auditoria == []


@pytest.mark.parametrize(("metodo", "ruta", "kw"), ESCRITURAS)
async def test_sin_sesion_da_401_y_no_toca_nada(metodo, ruta, kw):
    e = _app_con()
    r = await http(e.app).request(
        metodo, ruta, content=kw.get("contenido"), json=kw.get("cuerpo"), headers={"Origin": ORIGEN}
    )
    assert r.status_code == 401
    assert e.peticiones == [] and e.conn.actualizaciones() == []


# --- aislamiento ---------------------------------------------------------------------------------------------------


async def test_el_tenant_sale_de_la_sesion_y_nunca_del_pedido():
    e = _app_con()
    r = await e.pedir(
        "POST",
        f"/local/logo?tenant_id={OTRO_TENANT}",
        contenido=PNG,
        headers={"X-Tenant-Id": str(OTRO_TENANT)},
    )
    assert r.status_code == 200
    assert f"/{TENANT}/" in e.peticiones[0].url.path
    assert str(OTRO_TENANT) not in str(e.peticiones[0].url)
    (_, args) = e.conn.actualizaciones()[0]
    assert args[0] == TENANT


async def test_cada_tenant_solo_toca_su_carpeta_de_storage():
    a, b = _app_con(), _app_con()
    assert (await a.pedir("POST", "/local/logo", contenido=PNG, tenant=TENANT)).status_code == 200
    assert (await b.pedir("POST", "/local/logo", contenido=JPG, tenant=OTRO_TENANT)).status_code == 200
    assert (await b.pedir("DELETE", "/local/logo", tenant=OTRO_TENANT)).status_code == 200
    rutas_a = [(p.method, p.url.path.split("/object/")[-1]) for p in a.peticiones]
    assert rutas_a[0] == ("POST", f"logos/{TENANT}/logo.png")
    assert a.conn.settings == {"logo_ruta": f"{TENANT}/logo.png"}
    assert rutas_a[1][0] == "DELETE"
    for p in b.peticiones:
        assert str(TENANT) not in str(p.url) + p.content.decode("latin-1")
    assert (b.peticiones[0].method, b.peticiones[0].url.path.endswith(f"logos/{OTRO_TENANT}/logo.jpg")) == (
        "POST",
        True,
    )
    assert "logo_ruta" not in b.conn.settings
    for p in a.peticiones:
        assert str(OTRO_TENANT) not in str(p.url) + p.content.decode("latin-1")


async def test_apariencia_toma_el_tenant_de_la_sesion_y_no_el_del_pedido():
    e = _app_con()
    r = await e.pedir(
        "PATCH",
        f"/local/apariencia?tenant_id={OTRO_TENANT}",
        cuerpo={"color_principal": "#AABBCC", "tenant_id": str(OTRO_TENANT), "id": str(OTRO_TENANT)},
        headers={"X-Tenant-Id": str(OTRO_TENANT)},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"settings": {"color_principal": "#AABBCC"}}
    assert len(e.conn.actualizaciones()) == 1
    (_, args) = e.conn.actualizaciones()[0]
    assert args[0] == TENANT
    assert str(OTRO_TENANT) not in json.dumps([a for _, a in e.conn.ejecutado], default=str)
    assert e.conn.auditoria and OTRO_TENANT not in e.conn.auditoria[0]


async def test_no_se_puede_apuntar_el_logo_a_otro_tenant_por_el_patch_de_settings():
    e = _app_con()
    r = await e.pedir("PATCH", "/local/settings", cuerpo={"settings": {"logo_ruta": f"{OTRO_TENANT}/logo.png"}})
    assert r.status_code == 422
    assert e.conn.actualizaciones() == []


# --- apariencia ----------------------------------------------------------------------------------------------------


async def test_apariencia_guarda_colores_y_plazo_y_audita():
    e = _app_con()
    cuerpo = {"color_principal": "#AABBCC", "color_secundario": "#001122", "cancelacion_plazo_horas": 48}
    r = await e.pedir("PATCH", "/local/apariencia", cuerpo=cuerpo)
    assert r.status_code == 200, r.text
    assert r.json() == {"settings": cuerpo}
    assert e.conn.settings == cuerpo
    assert json.loads(e.conn.auditoria[0][3]) == {"claves": sorted(cuerpo)}


@pytest.mark.parametrize("plazo", [0, 720])
async def test_apariencia_acepta_los_extremos_del_plazo(plazo):
    e = _app_con()
    r = await e.pedir("PATCH", "/local/apariencia", cuerpo={"cancelacion_plazo_horas": plazo})
    assert r.status_code == 200 and r.json()["settings"]["cancelacion_plazo_horas"] == plazo


async def test_apariencia_con_null_borra_la_clave_y_no_toca_las_otras():
    e = _app_con()
    e.conn.settings = {"color_principal": "#AABBCC", "cancelacion_plazo_horas": 12, "logo_ruta": f"{TENANT}/logo.png"}
    r = await e.pedir("PATCH", "/local/apariencia", cuerpo={"color_principal": None})
    assert r.status_code == 200
    assert e.conn.settings == {"cancelacion_plazo_horas": 12, "logo_ruta": f"{TENANT}/logo.png"}


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("color_principal", "rojo"),
        ("color_principal", "#FFF"),
        ("color_principal", "#GGGGGG"),
        ("color_principal", "#AABBCC\n"),
        ("color_secundario", "red; </style><script>alert(1)</script>"),
        ("color_secundario", 123456),
        ("cancelacion_plazo_horas", -1),
        ("cancelacion_plazo_horas", 721),
        ("cancelacion_plazo_horas", "24"),
        ("cancelacion_plazo_horas", 2.5),
        ("cancelacion_plazo_horas", True),
    ],
)
async def test_apariencia_invalida_da_422_con_codigo_propio_sin_reflejar_el_valor_y_sin_guardar(campo, valor):
    e = _app_con()
    r = await e.pedir("PATCH", "/local/apariencia", cuerpo={campo: valor})
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["codigo"] == "apariencia_invalida"
    assert campo in r.json()["detail"]["mensaje"]
    for fragmento in ("rojo", "GGGGGG", "script", "123456", "721"):
        if fragmento in str(valor):
            assert fragmento not in r.text
    assert e.conn.actualizaciones() == []


@pytest.mark.parametrize("cuerpo", [{}, {"otra_cosa": 1}, [], "texto"])
async def test_apariencia_sin_campos_validos_da_422_sin_reflejar(cuerpo):
    e = _app_con()
    r = await e.pedir("PATCH", "/local/apariencia", cuerpo=cuerpo)
    assert r.status_code == 422
    assert '"input"' not in r.text
    assert e.conn.actualizaciones() == []


async def test_los_errores_de_validacion_no_reflejan_el_cuerpo_de_la_subida():
    e = _app_con()
    secreto = b"\x89PNG\r\n\x1a\nCONTENIDO-SENSIBLE" + b"\x00" * 10
    r = await e.pedir("POST", "/local/logo", contenido=b"x" + secreto)
    assert r.status_code == 422
    assert "SENSIBLE" not in r.text


def test_la_config_de_prueba_no_lleva_la_clave_de_storage():
    assert CLAVE not in repr(config_prueba())
    almacenamiento = ClienteAlmacenamiento("https://proy.supabase.co", CLAVE)
    assert CLAVE not in repr(almacenamiento)


def test_el_arranque_crea_el_cliente_de_storage_con_timeout_y_sin_exponer_la_clave(monkeypatch):
    from dashboard_backend import main
    from dashboard_backend.tests.test_proveedor_calendario_unitario import _preparar_main

    _preparar_main(monkeypatch)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", CLAVE)
    app = main.crear_app_desde_entorno()
    cliente = app.state.almacenamiento
    assert isinstance(cliente, ClienteAlmacenamiento)
    assert cliente._plazo_total == main.TIMEOUT_STORAGE_SEG
    assert CLAVE not in repr(cliente) and CLAVE not in repr(app.state.config)


def test_una_config_de_storage_invalida_impide_arrancar(monkeypatch):
    from turnos_almacenamiento import ConfiguracionAlmacenamientoInvalida

    from dashboard_backend import main
    from dashboard_backend.tests.test_proveedor_calendario_unitario import _preparar_main

    _preparar_main(monkeypatch)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "   ")
    with pytest.raises(ConfiguracionAlmacenamientoInvalida):
        main.crear_app_desde_entorno()
