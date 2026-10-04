"""platform_audit sin base de datos: la validación de PII y la forma de la llamada a `write_audit_log`."""

import json
import uuid

import pytest

from platform_audit import MetadataConPII, registrar


class ConexionFalsa:
    def __init__(self, resultado=None):
        self.llamadas = []
        self.resultado = resultado or uuid.uuid4()

    async def fetchval(self, sql, *args):
        self.llamadas.append((sql, args))
        return self.resultado


async def test_registrar_llama_a_write_audit_log_y_nunca_inserta_directo():
    conn = ConexionFalsa()
    entidad = uuid.uuid4()
    id_ = await registrar(conn, "cancelar_turno", "bookings", entidad, {"motivo": "pedido"})
    assert id_ == conn.resultado
    (sql, args), = conn.llamadas
    assert "public.write_audit_log" in sql
    assert "insert" not in sql.lower()
    assert args[0] == "cancelar_turno"
    assert args[1] == "bookings"
    assert args[2] == entidad
    assert json.loads(args[3]) == {"motivo": "pedido"}
    assert args[4] is None


async def test_registrar_acepta_metadata_y_entidad_ausentes():
    conn = ConexionFalsa()
    await registrar(conn, "login")
    (_, args), = conn.llamadas
    assert args == ("login", None, None, None, None)


async def test_registrar_pasa_el_tenant_explicito():
    conn = ConexionFalsa()
    tenant = uuid.uuid4()
    await registrar(conn, "suspender", "tenants", tenant, None, tenant_id=tenant)
    assert conn.llamadas[0][1][4] == tenant


@pytest.mark.parametrize(
    "metadata",
    [
        {"phone": "+5491155550000"},
        {"Name": "Ana"},
        {"detalle": {"channel_user_id": "123"}},
        {"items": [{"ok": 1}, {"body": "hola"}]},
        {"message_text": "x"},
        {"email": "a@b.com"},
    ],
)
async def test_registrar_rechaza_metadata_con_claves_de_pii_sin_tocar_la_base(metadata):
    conn = ConexionFalsa()
    with pytest.raises(MetadataConPII):
        await registrar(conn, "accion", metadata=metadata)
    assert conn.llamadas == []


async def test_el_error_de_pii_nombra_la_clave_pero_no_el_valor():
    with pytest.raises(MetadataConPII) as exc:
        await registrar(ConexionFalsa(), "accion", metadata={"phone": "+5491155550000"})
    assert "phone" in str(exc.value)
    assert "5491155550000" not in str(exc.value)
    assert "5491155550000" not in repr(exc.value)


@pytest.mark.parametrize("action", ["", "   ", None])
async def test_registrar_exige_action(action):
    conn = ConexionFalsa()
    with pytest.raises(ValueError):
        await registrar(conn, action)
    assert conn.llamadas == []


async def test_registrar_exige_metadata_objeto():
    with pytest.raises(TypeError):
        await registrar(ConexionFalsa(), "accion", metadata=["lista"])


async def test_registrar_rechaza_metadata_no_serializable_sin_filtrar_valores():
    with pytest.raises(TypeError) as exc:
        await registrar(ConexionFalsa(), "accion", metadata={"x": object()})
    assert "object object at" not in str(exc.value)
