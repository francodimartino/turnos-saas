"""platform_secrets sin base de datos: interfaz, valor opaco y traducción de errores."""

import json
import logging
import pickle
import uuid

import pytest

from platform_secrets import (
    AccesoSecretoDenegado,
    AlmacenSecretos,
    ErrorSecretos,
    Secreto,
    crear_almacen,
)

asyncpg = pytest.importorskip("asyncpg")

VALOR = "tok-super-secreto-123"


class ConexionFalsa:
    def __init__(self, resultado=None, error=None):
        self.llamadas = []
        self.resultado = resultado
        self.error = error

    async def fetchval(self, sql, *args):
        self.llamadas.append((sql, args))
        if self.error is not None:
            raise self.error
        return self.resultado


def test_la_interfaz_es_abstracta():
    with pytest.raises(TypeError):
        AlmacenSecretos()


def test_secreto_no_expone_el_valor_en_repr_str_format_ni_pickle():
    s = Secreto(VALOR)
    for texto in (repr(s), str(s), f"{s}", f"{s!r}", "%s" % s, "%r" % s):  # noqa: UP031
        assert VALOR not in texto
    assert s.revelar() == VALOR
    with pytest.raises(TypeError):
        pickle.dumps(s)
    assert VALOR not in repr([s])
    assert VALOR not in repr({"k": s})


def test_secreto_rechaza_valores_que_no_son_texto():
    with pytest.raises(TypeError):
        Secreto(123)


async def test_obtener_devuelve_secreto_opaco(caplog):
    conn = ConexionFalsa(resultado=VALOR)
    integracion = uuid.uuid4()
    with caplog.at_level(logging.DEBUG):
        s = await crear_almacen(conn).obtener(integracion)
    assert isinstance(s, Secreto)
    assert s.revelar() == VALOR
    assert VALOR not in repr(s)
    ((sql, args),) = conn.llamadas
    assert "public.get_integration_secret" in sql
    assert args == (integracion,)
    assert VALOR not in caplog.text


async def test_obtener_devuelve_none_si_la_integracion_no_tiene_secret():
    assert await crear_almacen(ConexionFalsa(resultado=None)).obtener(uuid.uuid4()) is None


async def test_obtener_de_otro_tenant_levanta_acceso_denegado_sin_detalles_de_la_base():
    error = asyncpg.InsufficientPrivilegeError("integración inexistente o de otro tenant")
    with pytest.raises(AccesoSecretoDenegado) as exc:
        await crear_almacen(ConexionFalsa(error=error)).obtener(uuid.uuid4())
    assert exc.value.__cause__ is None
    assert isinstance(exc.value, ErrorSecretos)


async def test_guardar_llama_a_store_tenant_integration_con_config_json():
    integracion = uuid.uuid4()
    conn = ConexionFalsa(resultado=integracion)
    tenant = uuid.uuid4()
    r = await crear_almacen(conn).guardar(tenant, "telegram_bot", {"webhook_key": "k"}, VALOR)
    assert r == integracion
    ((sql, args),) = conn.llamadas
    assert "public.store_tenant_integration" in sql
    assert args[0] == tenant
    assert args[1] == "telegram_bot"
    assert json.loads(args[2]) == {"webhook_key": "k"}
    assert args[3] == VALOR


async def test_guardar_acepta_un_secreto_opaco_y_config_ausente():
    conn = ConexionFalsa(resultado=uuid.uuid4())
    await crear_almacen(conn).guardar(uuid.uuid4(), "mercadopago", None, Secreto(VALOR))
    assert conn.llamadas[0][1][3] == VALOR
    assert json.loads(conn.llamadas[0][1][2]) == {}


async def test_guardar_sin_permiso_levanta_acceso_denegado_y_no_filtra_el_valor():
    error = asyncpg.InsufficientPrivilegeError("rol no autorizado para guardar integraciones: dashboard_service")
    with pytest.raises(AccesoSecretoDenegado) as exc:
        await crear_almacen(ConexionFalsa(error=error)).guardar(uuid.uuid4(), "telegram_bot", {}, VALOR)
    assert VALOR not in str(exc.value)
    assert VALOR not in repr(exc.value)


async def test_guardar_con_error_de_la_base_levanta_error_secretos_sin_el_valor():
    error = asyncpg.InvalidParameterValueError("tipo de integración de tenant no soportado")
    with pytest.raises(ErrorSecretos) as exc:
        await crear_almacen(ConexionFalsa(error=error)).guardar(uuid.uuid4(), "otro", {}, VALOR)
    assert VALOR not in str(exc.value)
    assert VALOR not in repr(exc.value)
    assert exc.value.__cause__ is None


async def test_guardar_rechaza_secret_vacio_antes_de_ir_a_la_base():
    conn = ConexionFalsa()
    with pytest.raises(ValueError):
        await crear_almacen(conn).guardar(uuid.uuid4(), "telegram_bot", {}, "")
    assert conn.llamadas == []


async def test_buscar_llama_a_find_integration():
    integracion = uuid.uuid4()
    conn = ConexionFalsa(resultado=integracion)
    profesional = uuid.uuid4()
    assert await crear_almacen(conn).buscar("google_calendar", profesional) == integracion
    ((sql, args),) = conn.llamadas
    assert "public.find_integration" in sql
    assert args == ("google_calendar", profesional)


async def test_guardar_y_eliminar_profesional_usan_las_funciones_de_profesional():
    conn = ConexionFalsa(resultado=uuid.uuid4())
    almacen = crear_almacen(conn)
    prof = uuid.uuid4()
    await almacen.guardar_profesional(prof, {"calendar_id": "x"}, VALOR)
    sql, args = conn.llamadas[0]
    assert "public.store_professional_integration" in sql
    assert args[0] == "google_calendar"
    assert args[1] == prof
    assert args[3] == VALOR
    conn.resultado = True
    assert await almacen.eliminar_profesional(prof) is True
    assert "public.delete_professional_integration" in conn.llamadas[1][0]
