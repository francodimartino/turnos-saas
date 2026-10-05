"""Catálogo, disponibilidad y settings sin base real: permisos por endpoint, validación de entrada y traducción
de errores. El comportamiento contra Postgres está en `test_catalogo_integracion.py`."""

import uuid

import asyncpg.exceptions as pgx
import pytest

from dashboard_backend.tests.soporte import ORIGEN, ConnFalsa, PoolFalso, app_de_prueba, http, token

TENANT = uuid.uuid4()
USUARIO = uuid.uuid4()
ID = uuid.uuid4()
OTRO_ID = uuid.uuid4()

SERVICIO = {"name": "Corte", "duration_minutes": 30, "price": 15000}
PROFESIONAL = {"name": "Ana"}
REGLA = {"professional_id": str(ID), "day_of_week": 1, "start_time": "09:00", "end_time": "18:00"}
BLOQUEO = {"start_at": "2026-12-24T00:00:00-03:00", "end_at": "2026-12-26T00:00:00-03:00", "reason": "Feriado"}

# (método, ruta, cuerpo, permiso que exige). Es el contrato de permisos de los endpoints de escritura.
ESCRITURAS = [
    ("POST", "/servicios", SERVICIO, "edit_prices"),
    ("PATCH", f"/servicios/{ID}", {"price": 1}, "edit_prices"),
    ("DELETE", f"/servicios/{ID}", None, "edit_prices"),
    ("POST", "/profesionales", PROFESIONAL, "manage_staff"),
    ("PATCH", f"/profesionales/{ID}", {"is_active": False}, "manage_staff"),
    ("DELETE", f"/profesionales/{ID}", None, "manage_staff"),
    ("PUT", f"/profesionales/{ID}/servicios/{OTRO_ID}", {"confirmation_mode": "auto"}, "manage_staff"),
    ("DELETE", f"/profesionales/{ID}/servicios/{OTRO_ID}", None, "manage_staff"),
    ("POST", "/reglas-disponibilidad", REGLA, "manage_staff"),
    ("PATCH", f"/reglas-disponibilidad/{ID}", {"day_of_week": 2}, "manage_staff"),
    ("DELETE", f"/reglas-disponibilidad/{ID}", None, "manage_staff"),
    ("POST", "/bloqueos-disponibilidad", BLOQUEO, "manage_staff"),
    ("PATCH", f"/bloqueos-disponibilidad/{ID}", {"reason": "x"}, "manage_staff"),
    ("DELETE", f"/bloqueos-disponibilidad/{ID}", None, "manage_staff"),
    ("PATCH", "/local/settings", {"settings": {"booking_approval_timeout_hours": 12}}, "manage_staff"),
]

LECTURAS = [
    "/servicios",
    f"/servicios/{ID}",
    "/profesionales",
    f"/profesionales/{ID}",
    f"/profesionales/{ID}/servicios",
    "/reglas-disponibilidad",
    f"/reglas-disponibilidad/{ID}",
    "/bloqueos-disponibilidad",
    f"/bloqueos-disponibilidad/{ID}",
    "/local/settings",
]


class ConnCatalogo(ConnFalsa):
    """ConnFalsa + `fetch`; `a_lanzar` simula que la base rechaza la próxima escritura de datos."""

    def __init__(self):
        super().__init__()
        self.a_lanzar: Exception | None = None
        self.fila: dict | None = None

    @staticmethod
    def _es_escritura(sql: str) -> bool:
        return sql.lstrip().lower().startswith(("insert", "update", "delete"))

    async def fetchrow(self, sql, *args):
        if self._es_escritura(sql) and self.a_lanzar is not None:
            raise self.a_lanzar
        previo = await super().fetchrow(sql, *args)
        if previo is not None:
            return previo
        return self.fila

    async def fetch(self, sql, *args):
        self.ejecutado.append((sql, args))
        return []

    async def execute(self, sql, *args):
        if self._es_escritura(sql) and self.a_lanzar is not None:
            raise self.a_lanzar
        await super().execute(sql, *args)

    def escrituras(self) -> list[str]:
        return [s for s in self.sentencias() if self._es_escritura(s)]


@pytest.fixture
def entorno():
    pool = PoolFalso()
    pool.conn = ConnCatalogo()
    app, _, _ = app_de_prueba(pool=pool)
    return app, pool.conn


def _h(**kw):
    return {"Origin": ORIGEN, "Cookie": f"dash_acceso={token(user_id=USUARIO, tenant_id=TENANT, **kw)}"}


async def _pedir(app, metodo, ruta, cuerpo=None, headers=None):
    return await http(app).request(metodo, ruta, json=cuerpo, headers=_h() if headers is None else headers)


@pytest.mark.parametrize("metodo,ruta,cuerpo,permiso", ESCRITURAS)
async def test_cada_escritura_exige_su_permiso_y_sin_el_no_toca_la_base(entorno, metodo, ruta, cuerpo, permiso):
    app, conn = entorno
    conn.estado_staff["tiene"] = False
    r = await _pedir(app, metodo, ruta, cuerpo)
    assert r.status_code == 403
    assert r.json()["detail"]["codigo"] == "permiso_denegado"
    assert conn.escrituras() == []


@pytest.mark.parametrize("metodo,ruta,cuerpo,permiso", ESCRITURAS)
async def test_el_permiso_consultado_es_el_documentado(entorno, metodo, ruta, cuerpo, permiso):
    app, conn = entorno
    conn.fila = {"id": ID, "settings": {}}
    await _pedir(app, metodo, ruta, cuerpo)
    consultas = [args for sql, args in conn.ejecutado if "tenant_user_permissions" in sql]
    assert any(permiso in args for args in consultas)


@pytest.mark.parametrize("metodo,ruta,cuerpo,permiso", ESCRITURAS)
async def test_sin_sesion_toda_escritura_es_401(entorno, metodo, ruta, cuerpo, permiso):
    app, conn = entorno
    r = await _pedir(app, metodo, ruta, cuerpo, headers={"Origin": ORIGEN})
    assert r.status_code == 401
    assert conn.escrituras() == []


@pytest.mark.parametrize("ruta", LECTURAS)
async def test_las_lecturas_las_puede_hacer_cualquier_staff_activo(entorno, ruta):
    app, conn = entorno
    conn.estado_staff["tiene"] = False  # sin ningún permiso granular
    conn.fila = {"id": ID, "settings": {}}
    r = await _pedir(app, "GET", ruta)
    assert r.status_code in (200, 404), r.text


@pytest.mark.parametrize("ruta", LECTURAS)
async def test_sin_sesion_toda_lectura_es_401(entorno, ruta):
    app, _ = entorno
    r = await _pedir(app, "GET", ruta, headers={})
    assert r.status_code == 401


async def test_el_modo_deposit_se_rechaza_con_422_sin_llegar_a_la_base(entorno):
    app, conn = entorno
    r = await _pedir(app, "PUT", f"/profesionales/{ID}/servicios/{OTRO_ID}", {"confirmation_mode": "deposit"})
    assert r.status_code == 422
    assert "deposit" in r.text
    assert conn.escrituras() == []


@pytest.mark.parametrize("modo", ["otro", "", None, 3])
async def test_modos_de_confirmacion_desconocidos_son_422(entorno, modo):
    app, _ = entorno
    r = await _pedir(app, "PUT", f"/profesionales/{ID}/servicios/{OTRO_ID}", {"confirmation_mode": modo})
    assert r.status_code == 422


@pytest.mark.parametrize("modo", ["auto", "approval"])
async def test_auto_y_approval_se_aceptan(entorno, modo):
    app, conn = entorno
    conn.fila = {"professional_id": ID, "service_id": OTRO_ID, "confirmation_mode": modo}
    r = await _pedir(app, "PUT", f"/profesionales/{ID}/servicios/{OTRO_ID}", {"confirmation_mode": modo})
    assert r.status_code == 200, r.text
    assert r.json()["confirmation_mode"] == modo


async def test_el_tenant_del_body_se_ignora_y_se_usa_el_de_la_sesion(entorno):
    app, conn = entorno
    otro = uuid.uuid4()
    conn.fila = {"id": ID, "tenant_id": TENANT, **SERVICIO, "deposit_amount": None, "is_active": True}
    r = await _pedir(app, "POST", "/servicios", {**SERVICIO, "tenant_id": str(otro)})
    assert r.status_code == 201, r.text
    insert = [a for s, a in conn.ejecutado if s.lstrip().lower().startswith("insert")]
    assert insert and TENANT in insert[0] and otro not in insert[0]


async def test_un_check_de_la_base_se_traduce_a_422_con_mensaje(entorno):
    app, conn = entorno
    conn.a_lanzar = pgx.CheckViolationError("x")
    conn.a_lanzar.constraint_name = "services_duration_minutes_check"
    r = await _pedir(app, "POST", "/servicios", {**SERVICIO, "duration_minutes": 0})
    assert r.status_code == 422
    assert "duración" in r.json()["detail"]["mensaje"]


async def test_borrar_algo_en_uso_es_409(entorno):
    app, conn = entorno
    conn.a_lanzar = pgx.ForeignKeyViolationError("x")
    r = await _pedir(app, "DELETE", f"/servicios/{ID}")
    assert r.status_code == 409
    assert r.json()["detail"]["codigo"] == "en_uso"


async def test_borrar_algo_inexistente_es_404(entorno):
    app, conn = entorno
    conn.fila = None
    r = await _pedir(app, "DELETE", f"/servicios/{ID}")
    assert r.status_code == 404


@pytest.mark.parametrize(
    "ruta,cuerpo",
    [
        ("/servicios", {"name": "  ", "duration_minutes": 30, "price": 1}),
        ("/servicios", {"name": "Corte", "duration_minutes": "x", "price": 1}),
        ("/servicios", {"name": "Corte", "duration_minutes": 30}),
        ("/servicios", {"name": "Corte", "duration_minutes": 30, "price": "NaN"}),
        ("/profesionales", {"name": ""}),
        ("/reglas-disponibilidad", {**REGLA, "start_time": "no-es-hora"}),
        ("/reglas-disponibilidad", {**REGLA, "professional_id": "no-uuid"}),
        ("/bloqueos-disponibilidad", {**BLOQUEO, "start_at": "2026-12-24T00:00:00"}),  # sin zona horaria
        ("/bloqueos-disponibilidad", {**BLOQUEO, "start_at": "2026-12-27T00:00:00-03:00"}),  # invertido
    ],
)
async def test_entradas_mal_formadas_son_422(entorno, ruta, cuerpo):
    app, conn = entorno
    r = await _pedir(app, "POST", ruta, cuerpo)
    assert r.status_code == 422
    assert conn.escrituras() == []


async def test_un_patch_vacio_es_422(entorno):
    app, conn = entorno
    r = await _pedir(app, "PATCH", f"/servicios/{ID}", {})
    assert r.status_code == 422
    assert conn.escrituras() == []


@pytest.mark.parametrize(
    "settings",
    [
        {"booking_approval_timeout_hours": "veinticuatro"},
        {"booking_approval_timeout_hours": True},
        {"booking_approval_timeout_hours": 1.5},
    ],
)
async def test_settings_con_tipo_invalido_es_422(entorno, settings):
    app, conn = entorno
    r = await _pedir(app, "PATCH", "/local/settings", {"settings": settings})
    assert r.status_code == 422
    assert conn.escrituras() == []


async def test_el_error_del_trigger_de_settings_fuera_de_rango_es_422_no_500(entorno):
    app, conn = entorno
    conn.fila = {"settings": {}}
    conn.a_lanzar = pgx.CheckViolationError("settings.booking_approval_timeout_hours debe ser un entero entre 1 y 168")
    r = await _pedir(app, "PATCH", "/local/settings", {"settings": {"booking_approval_timeout_hours": 169}})
    assert r.status_code == 422
    assert "1 y 168" in r.json()["detail"]["mensaje"]


async def test_settings_solo_actualiza_la_columna_settings(entorno):
    app, conn = entorno
    conn.fila = {"settings": {"direccion": "Calle 1"}}
    r = await _pedir(app, "PATCH", "/local/settings", {"settings": {"telefono": "123", "direccion": None}})
    assert r.status_code == 200, r.text
    updates = [s.lower() for s in conn.sentencias() if s.lstrip().lower().startswith("update")]
    assert len(updates) == 1
    assert "set settings" in " ".join(updates[0].split()) and "status" not in updates[0]
