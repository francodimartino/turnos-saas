"""Repositorio de identidad con una conexión simulada: qué SQL emite, con qué parámetros y qué deja en `audit_log`.

El SQL real corre en `test_identidad_integracion.py` (Postgres del CI). Acá se fija lo que no depende de la base:
que todo pasa por `canales_cliente`, que el tenant viaja siempre como parámetro y que cada escritura deja su
constancia con el actor de la persona y sin datos personales.
"""

import re
import uuid
from pathlib import Path

import pytest

from turnos_bot.identidad import repositorio

TENANT = uuid.uuid4()
FICHA = uuid.uuid4()
USUARIO = "7001"
TEL = "+5491100000000"

RAIZ_BOT = Path(repositorio.__file__).resolve().parents[1]


def _fila(**extra):
    base = {"id": FICHA, "tenant_id": TENANT, "phone": TEL, "name": None, "extra_data": {}, "deleted_at": None}
    return {**base, **extra}


class ConexionFalsa:
    """Registra las sentencias; `respuestas` es la cola de lo que devuelve cada `fetchrow`."""

    def __init__(self, respuestas=()) -> None:
        self.respuestas = list(respuestas)
        self.sentencias: list[tuple[str, tuple]] = []
        self.auditorias: list[tuple] = []

    async def fetchrow(self, sql, *args):
        self.sentencias.append((sql, args))
        return self.respuestas.pop(0) if self.respuestas else None

    async def execute(self, sql, *args):
        self.sentencias.append((sql, args))

    async def fetchval(self, sql, *args):
        self.auditorias.append(args)  # (acción, entidad, id, metadata json, tenant)
        return uuid.uuid4()

    def sql(self, fragmento: str) -> list[tuple[str, tuple]]:
        return [s for s in self.sentencias if fragmento in s[0]]

    def actor_fijado(self) -> list[str]:
        return [a[0] for sql, a in self.sentencias if "app.current_actor_id" in sql]


async def test_buscar_activo_consulta_canales_cliente_filtrando_por_tenant():
    conn = ConexionFalsa([_fila(canal_id=uuid.uuid4(), canal_verificado_at="2026-01-01")])
    cliente = await repositorio.buscar_activo(conn, TENANT, "telegram", USUARIO)
    assert cliente is not None and cliente.id == FICHA
    sql, args = conn.sentencias[0]
    assert "public.canales_cliente" in sql and "cc.tenant_id = $1" in sql and "ec.deleted_at is null" in sql
    assert args == (TENANT, "telegram", USUARIO)
    assert conn.auditorias == []  # leer no escribe


async def test_buscar_activo_sin_canal_devuelve_none():
    assert await repositorio.buscar_activo(ConexionFalsa(), TENANT, "telegram", USUARIO) is None


async def test_buscar_activo_verifica_un_canal_declarado_y_lo_audita():
    conn = ConexionFalsa([_fila(canal_id=uuid.uuid4(), canal_verificado_at=None)])
    await repositorio.buscar_activo(conn, TENANT, "telegram", USUARIO)
    (verificar,) = conn.sql("update public.canales_cliente set verificado_at")
    assert verificar[1][0] == TENANT
    assert [a[0] for a in conn.auditorias] == [repositorio.ACCION_VERIFICAR_CANAL]
    assert conn.actor_fijado() == [str(FICHA)]


async def test_crear_inserta_la_ficha_y_su_canal_verificado_y_audita_con_la_persona_como_actor():
    conn = ConexionFalsa([_fila()])
    cliente = await repositorio.crear(conn, TENANT, "telegram", USUARIO, TEL)
    assert cliente.id == FICHA
    (alta,) = conn.sql("insert into public.end_customers")
    assert alta[1] == (TENANT, TEL)
    (canal,) = conn.sql("insert into public.canales_cliente")
    assert "verificado_at" in canal[0] and "now()" in canal[0]
    assert canal[1] == (TENANT, FICHA, "telegram", USUARIO)
    accion, entidad, entidad_id, metadata, _ = conn.auditorias[0]
    assert (accion, entidad, entidad_id) == (repositorio.ACCION_CREAR_PERSONA, "end_customers", FICHA)
    assert metadata == '{"canal": "telegram"}'
    assert conn.actor_fijado() == [str(FICHA)]


async def test_vincular_sin_ficha_con_ese_telefono_devuelve_none_y_no_audita():
    conn = ConexionFalsa()
    assert await repositorio.vincular_alta_manual(conn, TENANT, "telegram", USUARIO, TEL) is None
    assert conn.auditorias == []


async def test_vincular_toma_un_lock_por_tenant_y_telefono_antes_de_chequear_los_canales():
    conn = ConexionFalsa([_fila()])
    await repositorio.vincular_alta_manual(conn, TENANT, "telegram", USUARIO, TEL)
    (lock, consulta) = conn.sentencias[:2]
    assert "pg_advisory_xact_lock" in lock[0] and lock[1] == (TENANT, TEL)
    assert "canales_cliente" in consulta[0]  # el chequeo viene después del lock


async def test_anonimizar_informa_el_canal_desde_el_que_se_pidio_el_borrado():
    conn = ConexionFalsa()
    await repositorio.anonimizar(conn, FICHA, "telegram", USUARIO)
    fijados = {a[0] for sql, a in conn.sentencias if "set_config" in sql}
    assert {str(FICHA), "telegram", USUARIO} <= fijados
    assert "public.anonymize_end_customer($1)" in conn.sentencias[-1][0]


async def test_vincular_agrega_el_canal_a_la_ficha_existente_y_audita():
    conn = ConexionFalsa([_fila()])
    cliente = await repositorio.vincular_alta_manual(conn, TENANT, "telegram", USUARIO, TEL)
    assert cliente is not None and cliente.id == FICHA
    sql, args = conn.sentencias[1]
    # solo fichas vivas del tenant y sin otro canal de chat; el email no impide la unión por teléfono
    assert "ec.tenant_id = $1" in sql and "ec.deleted_at is null" in sql
    assert "cc.canal in ('telegram', 'whatsapp')" in sql and "'email'" not in sql
    assert args == (TENANT, "telegram", USUARIO, TEL)
    assert [a[0] for a in conn.auditorias] == [repositorio.ACCION_VINCULAR_CANAL]


async def test_reactivar_pasa_por_la_funcion_de_la_base_con_el_actor_fijado_antes():
    conn = ConexionFalsa([_fila(phone=None)])
    cliente = await repositorio.reactivar(conn, TENANT, FICHA, "telegram", USUARIO)
    assert cliente.phone is None and not cliente.completo
    assert "public.reactivate_end_customer($1, $2, $3)" in conn.sentencias[1][0]
    assert "app.current_actor_id" in conn.sentencias[0][0]  # el actor se fija antes de llamar a la función


@pytest.mark.parametrize("fila", [None, _fila(tenant_id=uuid.uuid4())])
async def test_reactivar_algo_que_ya_no_esta_o_es_de_otro_tenant_falla(fila):
    conn = ConexionFalsa([fila])
    with pytest.raises(LookupError):
        await repositorio.reactivar(conn, TENANT, FICHA, "telegram", USUARIO)


@pytest.mark.parametrize(
    ("funcion", "dato", "campo"),
    [(repositorio.guardar_telefono, TEL, "phone"), (repositorio.guardar_nombre, "Ana Prueba", "name")],
)
async def test_guardar_dato_filtra_por_tenant_y_audita_solo_el_nombre_del_campo(funcion, dato, campo):
    conn = ConexionFalsa()
    await funcion(conn, TENANT, FICHA, dato)
    assert conn.sentencias[0][1] == (TENANT, FICHA, dato)
    accion, _, _, metadata, _ = conn.auditorias[0]
    assert accion == repositorio.ACCION_GUARDAR_DATO and metadata == f'{{"campo": "{campo}"}}'
    assert dato not in metadata


def test_el_bot_no_lee_ni_escribe_las_columnas_viejas_de_end_customers():
    """Ningún módulo del bot que toque `end_customers` nombra las columnas de canal que ya no usa."""
    prohibido = re.compile(r"\bchannel\b|\bchannel_user_id\b|\bchannel_user_hash\b")
    revisados = 0
    for archivo in sorted(RAIZ_BOT.rglob("*.py")):
        if "tests" in archivo.relative_to(RAIZ_BOT).parts:
            continue
        texto = archivo.read_text(encoding="utf-8")
        if "end_customers" not in texto:
            continue
        revisados += 1
        assert not prohibido.search(texto), f"{archivo.name} nombra columnas de canal de end_customers"
    assert revisados >= 2  # el repositorio de identidad y la consulta de Calendar
