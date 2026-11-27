"""Marca del local sin base real: logo (Storage simulado que conserva los objetos), colores y plazo de cancelación.

Cubre el caso feliz, tipo inválido, tamaño, Storage caído o sin respuesta, 403, aislamiento entre tenants, auditoría,
el orden (Storage fuera de la transacción), las operaciones cruzadas del mismo negocio (con nombres de objeto únicos
ninguna borra lo que otra guardó) y que la clave de Storage no salga a logs ni a respuestas.
"""

import asyncio
import json
import logging
import re
import time
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
UNA_HORA = 3600


def _patron(ext, tenant=TENANT):
    return re.compile(rf"{tenant}/logo-[0-9a-f]{{32}}\.{ext}\Z")


def _viejo(ext="png", sufijo="a"):
    return f"{TENANT}/logo-{sufijo * 32}.{ext}"


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


class StorageSimulado:
    """Storage que conserva los objetos: sube, lista (con `created_at`) y borra, como la API REST de Supabase.

    `hook(req)` (opcional, async) se invoca antes de atender cada pedido: puede bloquearlo o devolver una respuesta.
    """

    def __init__(self, hook=None):
        self.objetos: dict[str, float] = {}  # ruta -> momento de creación (segundos epoch)
        self.peticiones: list[httpx.Request] = []
        self.eventos: list[str] = []
        self.hook = hook

    def sembrar(self, ruta, edad_seg=UNA_HORA):
        self.objetos[ruta] = time.time() - edad_seg

    async def atender(self, req: httpx.Request) -> httpx.Response:
        self.peticiones.append(req)
        if self.hook is not None:
            respuesta = await self.hook(req)
            if respuesta is not None:
                return respuesta
        partes = req.url.path.removeprefix("/storage/v1/object/")
        if req.method == "POST" and partes.startswith("list/logos"):
            self.eventos.append("storage_list")
            prefijo = json.loads(req.content)["prefix"] + "/"
            lista = [
                {
                    "name": r.removeprefix(prefijo),
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(t)),
                }
                for r, t in self.objetos.items()
                if r.startswith(prefijo)
            ]
            return httpx.Response(200, json=lista)
        if req.method == "POST":
            self.eventos.append("storage_post")
            self.objetos[partes.removeprefix("logos/")] = time.time()
            return httpx.Response(200, json={"Key": "x"})
        if req.method == "DELETE":
            self.eventos.append("storage_delete")
            for ruta in json.loads(req.content)["prefixes"]:
                self.objetos.pop(ruta, None)
            return httpx.Response(200, json=[])
        return httpx.Response(404)


class Entorno:
    def __init__(self, hook=None, *, con_almacenamiento=True, timeout=10.0):
        self.eventos: list[str] = []
        self.storage = StorageSimulado(hook)
        self.peticiones = self.storage.peticiones
        pool = PoolFalso()
        pool.conn = ConnMarca(self.eventos)
        self.conn = pool.conn
        almacenamiento = None
        if con_almacenamiento:
            transporte = httpx.MockTransport(self._atender)
            almacenamiento = ClienteAlmacenamiento(
                "https://proy.supabase.co", CLAVE, timeout=timeout, http=httpx.AsyncClient(transport=transporte)
            )
        self.app, _, _ = _app_de_prueba(pool=pool, almacenamiento=almacenamiento)

    async def _atender(self, req: httpx.Request) -> httpx.Response:
        self.eventos.append("storage_" + req.method.lower())
        return await self.storage.atender(req)

    async def pedir(self, metodo, ruta, *, contenido=None, cuerpo=None, tenant=TENANT, headers=None, **kw):
        h = {"Origin": ORIGEN, "Cookie": f"dash_acceso={token(user_id=USUARIO, tenant_id=tenant, **kw)}"}
        h.update(headers or {})
        return await http(self.app).request(metodo, ruta, content=contenido, json=cuerpo, headers=h)

    def logo_vigente_existe(self) -> bool:
        ruta = self.conn.settings.get("logo_ruta")
        return ruta is None or ruta in self.storage.objetos


def _app_con(hook=None, **kw):
    return Entorno(hook, **kw)


def _responder(estado, *, solo=None):
    """Hook que contesta `estado` a los pedidos (todos, o los de ese método)."""

    async def hook(req):
        if solo is None or req.method == solo:
            return httpx.Response(estado, json={"message": f"detalle interno del proveedor {CLAVE}"})
        return None

    return hook


# --- subir ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("bytes_", "ext"), [(PNG, "png"), (JPG, "jpg"), (WEBP, "webp")])
async def test_subir_logo_guarda_en_storage_con_nombre_unico_y_la_ruta_en_settings(bytes_, ext):
    e = _app_con()
    r = await e.pedir("POST", "/local/logo", contenido=bytes_)
    assert r.status_code == 200, r.text
    ruta = r.json()["settings"]["logo_ruta"]
    assert _patron(ext).match(ruta)
    assert r.json() == {"settings": {"logo_ruta": ruta}}
    assert e.conn.settings == {"logo_ruta": ruta}
    assert list(e.storage.objetos) == [ruta]
    subida = e.peticiones[0]
    assert subida.method == "POST" and subida.url.path.endswith(f"/object/logos/{ruta}")
    assert subida.content == bytes_
    assert subida.headers["authorization"] == f"Bearer {CLAVE}"


async def test_dos_subidas_del_mismo_formato_no_usan_el_mismo_nombre():
    e = _app_con()
    r1 = await e.pedir("POST", "/local/logo", contenido=PNG)
    r2 = await e.pedir("POST", "/local/logo", contenido=PNG)
    assert r1.json()["settings"]["logo_ruta"] != r2.json()["settings"]["logo_ruta"]


async def test_subir_reemplaza_el_logo_anterior_y_borra_el_objeto_viejo_despues_del_commit():
    e = _app_con()
    e.storage.sembrar(_viejo("png"))
    e.conn.settings = {"logo_ruta": _viejo("png")}
    r = await e.pedir("POST", "/local/logo", contenido=JPG)
    assert r.status_code == 200, r.text
    nueva = r.json()["settings"]["logo_ruta"]
    assert list(e.storage.objetos) == [nueva]  # el anterior ya no está y el nuevo sí
    ev = e.eventos
    primera_subida = ev.index("storage_post")
    previos = ev[:primera_subida]
    # Storage nunca se toca con una transacción abierta: todo BEGIN previo ya tiene su COMMIT.
    assert previos.count("begin") == previos.count("commit")
    borrado = ev.index("storage_delete")
    assert borrado > ev.index("update_settings") > primera_subida
    assert ev[:borrado].count("begin") == ev[:borrado].count("commit")


async def test_el_guardado_de_la_ruta_toma_el_lock_por_tenant_dentro_de_la_transaccion():
    e = _app_con()
    await e.pedir("POST", "/local/logo", contenido=PNG)
    lock = [a for s_, a in e.conn.ejecutado if "pg_advisory_xact_lock" in s_]
    assert lock == [(f"logo:{TENANT}",)]
    ev = e.eventos
    assert ev.index("update_settings") > ev.index("select")


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
    assert r.json()["settings"]["logo_ruta"].endswith(".png")


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


async def test_storage_con_error_da_503_y_la_base_no_cambia(caplog):
    e = _app_con(_responder(500))
    e.conn.settings = {"color_principal": "#112233"}
    with caplog.at_level(logging.DEBUG):
        r = await e.pedir("POST", "/local/logo", contenido=PNG)
    assert r.status_code == 503, r.text
    assert r.json()["detail"]["codigo"] == "almacenamiento_no_disponible"
    assert e.conn.actualizaciones() == []
    assert e.conn.settings == {"color_principal": "#112233"}
    # Ni la clave ni el detalle del proveedor salen en la respuesta ni en los logs.
    assert CLAVE not in r.text and "detalle interno" not in r.text
    assert CLAVE not in caplog.text and "detalle interno" not in caplog.text


async def test_storage_sin_red_da_503_y_se_registra_como_posible_huerfano(caplog):
    async def sin_red(req):
        raise httpx.ConnectError("sin red", request=req)

    e = _app_con(sin_red)
    with caplog.at_level(logging.WARNING):
        r = await e.pedir("POST", "/local/logo", contenido=PNG)
    assert r.status_code == 503
    assert e.conn.actualizaciones() == []
    assert r.headers["Retry-After"] == "5"
    assert any(getattr(x, "motivo", None) == "logo_huerfano_posible" for x in caplog.records)


async def test_si_la_subida_supera_el_plazo_se_registra_el_posible_huerfano_con_su_ruta(caplog):
    async def lento(req):
        await asyncio.sleep(5)

    e = _app_con(lento, timeout=0.05)
    with caplog.at_level(logging.WARNING):
        r = await e.pedir("POST", "/local/logo", contenido=PNG)
    assert r.status_code == 503
    assert e.conn.actualizaciones() == []
    posibles = [x for x in caplog.records if getattr(x, "motivo", None) == "logo_huerfano_posible"]
    assert len(posibles) == 1
    assert _patron("png").match(posibles[0].ruta) and posibles[0].tenant_id == str(TENANT)
    assert CLAVE not in caplog.text


async def test_una_respuesta_de_error_del_proveedor_no_se_registra_como_posible_huerfano(caplog):
    e = _app_con(_responder(503))
    with caplog.at_level(logging.WARNING):
        r = await e.pedir("POST", "/local/logo", contenido=PNG)
    assert r.status_code == 503
    assert not any(getattr(x, "motivo", None) == "logo_huerfano_posible" for x in caplog.records)


async def test_sin_cliente_de_storage_subir_y_borrar_dan_503_no_500():
    e = _app_con(con_almacenamiento=False)
    assert (await e.pedir("POST", "/local/logo", contenido=PNG)).status_code == 503
    assert (await e.pedir("DELETE", "/local/logo")).status_code == 503
    assert e.conn.actualizaciones() == []


# --- la base falla después de que Storage respondió bien ------------------------------------------------------------


@pytest.mark.parametrize("ext_nueva", ["png", "jpg"])  # el mismo formato que el anterior y otro distinto
async def test_si_la_base_falla_tras_subir_el_logo_anterior_sigue_intacto_y_el_huerfano_se_registra(ext_nueva, caplog):
    e = _app_con()
    e.storage.sembrar(_viejo("png"))
    e.conn.settings = {"logo_ruta": _viejo("png")}
    e.conn.fallo_escritura = TimeoutError()
    contenido = PNG if ext_nueva == "png" else JPG
    with caplog.at_level(logging.WARNING):
        r = await e.pedir("POST", "/local/logo", contenido=contenido)
    assert r.status_code == 503, r.text
    assert e.conn.settings == {"logo_ruta": _viejo("png")}  # apunta a algo que existe
    assert e.logo_vigente_existe()
    assert "storage_delete" not in e.eventos  # el anterior no se tocó
    huerfanos = [x for x in caplog.records if getattr(x, "motivo", None) == "logo_huerfano"]
    assert len(huerfanos) == 1 and _patron(ext_nueva).match(huerfanos[0].ruta)


async def test_si_la_base_falla_al_borrar_no_se_toca_storage_y_la_ruta_sigue_valida():
    e = _app_con()
    e.storage.sembrar(_viejo("png"))
    e.conn.settings = {"logo_ruta": _viejo("png")}
    e.conn.fallo_escritura = TimeoutError()
    r = await e.pedir("DELETE", "/local/logo")
    assert r.status_code == 503
    assert e.peticiones == []
    assert e.conn.settings == {"logo_ruta": _viejo("png")} and e.logo_vigente_existe()


async def test_si_falla_la_limpieza_la_subida_igual_responde_200_y_la_ruta_es_valida(caplog):
    e = _app_con(_responder(500, solo="DELETE"))
    e.storage.sembrar(_viejo("png"))
    e.conn.settings = {"logo_ruta": _viejo("png")}
    with caplog.at_level(logging.WARNING):
        r = await e.pedir("POST", "/local/logo", contenido=JPG)
    assert r.status_code == 200
    assert e.logo_vigente_existe() and e.conn.settings["logo_ruta"] != _viejo("png")
    assert any(getattr(x, "motivo", None) == "logo_huerfano" for x in caplog.records)
    assert CLAVE not in caplog.text


# --- borrar --------------------------------------------------------------------------------------------------------


async def test_borrar_logo_quita_la_clave_primero_y_borra_el_objeto_que_tenia_despues_del_commit():
    e = _app_con()
    e.storage.sembrar(_viejo("png"))
    e.conn.settings = {"logo_ruta": _viejo("png"), "color_principal": "#112233"}
    r = await e.pedir("DELETE", "/local/logo")
    assert r.status_code == 200, r.text
    assert r.json() == {"settings": {"color_principal": "#112233"}}
    assert e.storage.objetos == {}
    borrados = [json.loads(p.content)["prefixes"] for p in e.peticiones if p.method == "DELETE"]
    assert borrados[0] == [_viejo("png")]
    assert json.loads(e.conn.auditoria[0][3]) == {"claves": ["logo_ruta"]}
    ev = e.eventos
    assert ev.index("storage_delete") > ev.index("update_settings")
    assert ev[: ev.index("storage_delete")].count("begin") == ev[: ev.index("storage_delete")].count("commit")


async def test_borrar_sin_logo_es_idempotente():
    e = _app_con()
    r = await e.pedir("DELETE", "/local/logo")
    assert r.status_code == 200 and r.json() == {"settings": {}}


async def test_si_storage_falla_al_borrar_la_clave_ya_se_quito_y_el_huerfano_se_registra(caplog):
    e = _app_con(_responder(500, solo="DELETE"))
    e.storage.sembrar(_viejo("png"))
    e.conn.settings = {"logo_ruta": _viejo("png")}
    with caplog.at_level(logging.WARNING):
        r = await e.pedir("DELETE", "/local/logo")
    assert r.status_code == 200, r.text
    assert "logo_ruta" not in e.conn.settings  # nunca una ruta colgada
    huerfanos = [x for x in caplog.records if getattr(x, "motivo", None) == "logo_huerfano"]
    assert huerfanos and huerfanos[0].ruta == _viejo("png")
    assert CLAVE not in r.text and CLAVE not in caplog.text


# --- operaciones cruzadas del mismo negocio --------------------------------------------------------------------------


async def test_un_borrado_mientras_una_subida_esta_en_vuelo_no_deja_la_ruta_apuntando_a_nada():
    a_en_storage, liberar = asyncio.Event(), asyncio.Event()

    async def hook(req):
        if req.method == "POST" and "/list/" not in req.url.path:
            a_en_storage.set()
            await liberar.wait()  # la subida queda a mitad de camino
        return

    e = _app_con(hook)
    e.storage.sembrar(_viejo("png"))
    e.conn.settings = {"logo_ruta": _viejo("png")}
    subida = asyncio.create_task(e.pedir("POST", "/local/logo", contenido=JPG))
    await a_en_storage.wait()
    r_borrar = await e.pedir("DELETE", "/local/logo")
    liberar.set()
    r_subir = await subida
    assert r_borrar.status_code == 200 and r_subir.status_code == 200
    assert e.conn.settings["logo_ruta"] in e.storage.objetos  # la subida (posterior al borrado) quedó viva
    assert _viejo("png") not in e.storage.objetos


async def test_una_subida_que_empieza_mientras_la_limpieza_de_otra_esta_en_vuelo_conserva_su_objeto():
    borrando, liberar = asyncio.Event(), asyncio.Event()
    bloqueos = []

    async def hook(req):
        if req.method == "DELETE" and not bloqueos:
            bloqueos.append(1)
            borrando.set()
            await liberar.wait()  # el borrado del objeto viejo de A queda en vuelo
        return

    e = _app_con(hook)
    e.storage.sembrar(_viejo("png"))
    e.conn.settings = {"logo_ruta": _viejo("png")}
    subida_a = asyncio.create_task(e.pedir("POST", "/local/logo", contenido=JPG))
    await borrando.wait()
    r_b = await e.pedir("POST", "/local/logo", contenido=WEBP)  # B sube y guarda con el borrado de A en vuelo
    liberar.set()
    r_a = await subida_a
    assert r_a.status_code == 200 and r_b.status_code == 200
    assert e.conn.settings["logo_ruta"] in e.storage.objetos
    assert e.logo_vigente_existe()


async def test_dos_subidas_cruzadas_terminan_con_la_ruta_apuntando_a_un_objeto_existente():
    a_en_storage, liberar = asyncio.Event(), asyncio.Event()

    async def hook(req):
        if req.method == "POST" and "/list/" not in req.url.path and req.content[:4] == b"\x89PNG":
            a_en_storage.set()
            await liberar.wait()
        return

    e = _app_con(hook)
    tarea_a = asyncio.create_task(e.pedir("POST", "/local/logo", contenido=PNG))
    await a_en_storage.wait()
    r_b = await e.pedir("POST", "/local/logo", contenido=JPG)
    liberar.set()
    r_a = await tarea_a
    assert r_a.status_code == 200 and r_b.status_code == 200
    assert e.conn.settings["logo_ruta"] in e.storage.objetos
    assert e.logo_vigente_existe()


async def test_un_svg_o_un_503_no_afectan_a_una_subida_legitima_en_curso():
    a_en_storage, liberar = asyncio.Event(), asyncio.Event()

    async def hook(req):
        if req.method == "POST" and "/list/" not in req.url.path:
            if req.content[:4] == b"\x89PNG":
                a_en_storage.set()
                await liberar.wait()
                return None
            if req.content[:3] == b"\xff\xd8\xff":
                return httpx.Response(503)  # una subida que Storage rechaza
        return None

    e = _app_con(hook)
    legitima = asyncio.create_task(e.pedir("POST", "/local/logo", contenido=PNG))
    await a_en_storage.wait()
    assert (await e.pedir("POST", "/local/logo", contenido=SVG)).status_code == 422
    assert (await e.pedir("POST", "/local/logo", contenido=JPG)).status_code == 503
    liberar.set()
    r = await legitima
    assert r.status_code == 200, r.text
    assert e.conn.settings["logo_ruta"] in e.storage.objetos


async def test_la_limpieza_respeta_la_gracia_y_no_borra_lo_reciente_que_no_es_la_ruta_vigente():
    e = _app_con()
    reciente = f"{TENANT}/logo-{'b' * 32}.png"  # subida de otra operación que todavía no guardó su ruta
    antiguo = f"{TENANT}/logo-{'c' * 32}.webp"  # huérfano viejo
    e.storage.sembrar(reciente, edad_seg=10)
    e.storage.sembrar(antiguo, edad_seg=UNA_HORA)
    r = await e.pedir("POST", "/local/logo", contenido=PNG)
    assert r.status_code == 200
    assert reciente in e.storage.objetos
    assert antiguo not in e.storage.objetos
    assert r.json()["settings"]["logo_ruta"] in e.storage.objetos


async def test_la_limpieza_no_toca_objetos_ajenos_al_logo_ni_de_otro_tenant():
    e = _app_con()
    ajeno = f"{TENANT}/otro-archivo.png"
    de_otro = f"{OTRO_TENANT}/logo-{'d' * 32}.png"
    e.storage.sembrar(ajeno)
    e.storage.sembrar(de_otro)
    await e.pedir("POST", "/local/logo", contenido=PNG)
    assert ajeno in e.storage.objetos and de_otro in e.storage.objetos


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
    ra = await a.pedir("POST", "/local/logo", contenido=PNG, tenant=TENANT)
    rb = await b.pedir("POST", "/local/logo", contenido=JPG, tenant=OTRO_TENANT)
    assert ra.status_code == 200 and rb.status_code == 200
    assert _patron("png", TENANT).match(ra.json()["settings"]["logo_ruta"])
    assert _patron("jpg", OTRO_TENANT).match(rb.json()["settings"]["logo_ruta"])
    assert list(a.storage.objetos) == [ra.json()["settings"]["logo_ruta"]]
    assert list(b.storage.objetos) == [rb.json()["settings"]["logo_ruta"]]
    assert (await b.pedir("DELETE", "/local/logo", tenant=OTRO_TENANT)).status_code == 200
    assert b.storage.objetos == {} and "logo_ruta" not in b.conn.settings
    for p in b.peticiones:
        assert str(TENANT) not in str(p.url) + p.content.decode("latin-1")
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
    r = await e.pedir(
        "PATCH", "/local/settings", cuerpo={"settings": {"logo_ruta": _viejo().replace(str(TENANT), str(OTRO_TENANT))}}
    )
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
