"""Anclaje de las cabezas de la cadena de auditoría sin base: pool y reportador simulados."""

import base64
import logging
import uuid
from contextlib import asynccontextmanager

from jobs_purga.anclaje import anclar_cadenas


class Conn:
    def __init__(self, filas, falla):
        self._filas, self._falla = filas, falla

    async def fetch(self, sql, *args):
        assert "audit_chain_heads" in sql
        if self._falla:
            raise RuntimeError("caída de la base")
        return self._filas


class Pool:
    def __init__(self, filas, falla=False):
        self._filas, self._falla = filas, falla

    @asynccontextmanager
    async def acquire(self):
        yield Conn(self._filas, self._falla)


class Reportador:
    def __init__(self):
        self.excepciones = []

    def capturar_excepcion(self, exc, **contexto):
        self.excepciones.append((exc, contexto))


def _hash(n):
    return bytes([n]) * 32


async def test_emite_un_log_info_por_cadena_con_solo_tenant_seq_y_hash(caplog):
    tenant = uuid.uuid4()
    pool = Pool(
        [
            {"tenant_id": tenant, "chain_seq": 7, "hash": _hash(1)},
            {"tenant_id": None, "chain_seq": 3, "hash": _hash(2)},
        ]
    )
    with caplog.at_level(logging.INFO, logger="jobs_purga"):
        ok = await anclar_cadenas(pool, Reportador())

    assert ok is True
    anclas = [r for r in caplog.records if r.message == "audit_chain_anchor"]
    assert len(anclas) == 2 and all(r.levelno == logging.INFO for r in anclas)
    campos = {"tenant_id", "chain_seq", "hash"}
    for r in anclas:
        extras = {k for k in r.__dict__ if k in campos | {"metadata", "phone", "email", "nombre", "cabezas"}}
        assert extras == campos
    assert (anclas[0].tenant_id, anclas[0].chain_seq) == (str(tenant), 7)
    assert (anclas[1].tenant_id, anclas[1].chain_seq) == (None, 3)
    assert base64.urlsafe_b64decode(anclas[0].hash + "=") == _hash(1)


async def test_sin_cadenas_no_emite_nada(caplog):
    with caplog.at_level(logging.INFO, logger="jobs_purga"):
        assert await anclar_cadenas(Pool([]), Reportador()) is True
    assert [r for r in caplog.records if r.message == "audit_chain_anchor"] == []


async def test_si_falla_la_lectura_lo_reporta_con_evento_propio_y_no_lanza(caplog):
    rep = Reportador()
    with caplog.at_level(logging.INFO, logger="jobs_purga"):
        ok = await anclar_cadenas(Pool([], falla=True), rep)

    assert ok is False
    assert [r.message for r in caplog.records if r.message.startswith("audit_chain_anchor")] == [
        "audit_chain_anchor_fallido"
    ]
    assert len(rep.excepciones) == 1 and rep.excepciones[0][1]["paso"] == "audit_chain_anchor"


async def test_el_hash_emitido_sobrevive_al_filtro_de_telefonos_del_formateador():
    import io
    import json

    from turnos_observabilidad import configurar_logging

    # 14 dígitos seguidos en hex (0x11 repetido da "17"): usamos bytes cuyo hex es solo dígitos.
    cabeza = bytes.fromhex("12345678901234567890123456789012" * 2)
    buf = io.StringIO()
    configurar_logging(stream=buf)
    await anclar_cadenas(Pool([{"tenant_id": None, "chain_seq": 1, "hash": cabeza}]), Reportador())
    linea = json.loads(buf.getvalue().strip().splitlines()[-1])
    assert base64.urlsafe_b64decode(linea["hash"] + "=") == cabeza
    assert linea["tenant_id"] is None and linea["chain_seq"] == 1
