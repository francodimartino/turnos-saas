"""Endurecimiento del filtrado de PII en logs y Sentry (Ley 25.326, ARCHITECTURE.md §6.1)."""

import io
import json
import logging

import pytest

from observabilidad import configurar_logging, log_evento_obligatorio, sentry_before_send

TELEFONOS = [
    "+5491155550000",
    "+54 9 11 5555-0000",
    "1155550000",
    "11 5555-0000",
    "(011) 5555-0000",
    "5491155550000",
    "+14155552671",
]


def _salida(**extra) -> dict:
    buf = io.StringIO()
    configurar_logging(stream=buf)
    logging.getLogger("t").info("hola", extra=extra)
    return json.loads(buf.getvalue().strip().splitlines()[-1])


def test_logs_ordinarios_eliminan_claves_pii():
    d = _salida(phone="+5491155550000", Name="Ana", text="hola", end_customer_id="abc")
    for clave in ("phone", "Name", "text"):
        assert clave not in d
    assert d["end_customer_id"] == "abc"


def test_logs_eliminan_pii_en_extra_anidado():
    d = _salida(motivo={"customer": {"phone": "1", "ok": 1}, "items": [{"Name": "x", "k": 2}]})
    assert d["motivo"] == {"customer": {"ok": 1}, "items": [{"k": 2}]}


# 2026-10-19 12:30:55.555512 UTC: el timestamp ISO trae la racha "5555" (microsegundos), igual que
# cualquier otro instante cuyos dígitos coincidan con los de un teléfono de prueba.
CREATED_CON_5555 = 1792413055.555512


class _FijaTiempo(logging.Filter):
    def __init__(self, created: float) -> None:
        super().__init__()
        self.created = created

    def filter(self, record: logging.LogRecord) -> bool:
        record.created = self.created
        return True


def _emitir_contacto(tel: str, created: float) -> dict:
    buf = io.StringIO()
    configurar_logging(stream=buf)
    logging.getLogger().handlers[-1].addFilter(_FijaTiempo(created))
    logging.getLogger("t").info("contacto %s fallo", tel, extra={"motivo": f"url=/c/{tel}/x"})
    return json.loads(buf.getvalue())


@pytest.mark.parametrize("created", [CREATED_CON_5555, 1792413055.0, 1700000000.123456])
@pytest.mark.parametrize("tel", TELEFONOS)
def test_logs_filtran_telefonos_en_valores_y_mensaje(tel, created):
    d = _emitir_contacto(tel, created)
    # Se verifica solo lo que controla el test (mensaje y valores): el timestamp, el pid u otros
    # campos del registro pueden contener cualquier racha de dígitos sin ser el teléfono.
    texto = d["message"] + d["motivo"]
    assert "5555" not in texto and "4155552671" not in texto
    assert "".join(c for c in tel if c.isdigit())[-7:] not in texto
    assert "[filtrado]" in d["message"] and "[filtrado]" in d["motivo"]
    assert not any(c.isdigit() for c in texto)


def test_timestamp_con_racha_de_digitos_no_se_altera():
    d = _emitir_contacto("+5491155550000", CREATED_CON_5555)
    assert "5555" in d["timestamp"]
    assert "[filtrado]" not in d["timestamp"]


def _instantes_200() -> list[float]:
    base = 1792413000.0
    rachas = ["5555", "0000", "1234", "5550", "1155", "9999", "0123"]
    fijos = [base + int(r) % 60 + float(f"0.{r}00") for r in rachas]  # rachas en segundos y microsegundos
    fijos += [base + float(f"{s}.{r}00") for r in rachas for s in ("55", "12", "00")]
    fijos = list(dict.fromkeys(fijos))
    resto = [base + 7.000001 + i * 1234.555512 for i in range(200 - len(fijos))]
    return fijos + resto


def test_logs_filtran_telefonos_con_200_instantes_distintos():
    instantes = _instantes_200()
    assert len(set(instantes)) == 200
    for i, created in enumerate(instantes):
        tel = TELEFONOS[i % len(TELEFONOS)]
        d = _emitir_contacto(tel, created)
        texto = d["message"] + d["motivo"]
        assert "5555" not in texto and "4155552671" not in texto, (created, tel)
        assert "[filtrado]" in d["message"] and "[filtrado]" in d["motivo"], (created, tel)
        assert not any(c.isdigit() for c in texto), (created, tel)
        assert "[filtrado]" not in d["timestamp"], created


def test_logs_filtran_telefono_en_traceback():
    buf = io.StringIO()
    configurar_logging(stream=buf)
    try:
        raise RuntimeError("no se pudo enviar a +5491155550000")
    except RuntimeError:
        logging.getLogger("t").exception("error")
    assert "5550000" not in buf.getvalue()


@pytest.mark.parametrize("tel", TELEFONOS)
def test_sentry_filtra_telefonos_en_valores(tel):
    evento = {
        "exception": {"values": [{"type": "E", "value": f"fallo con {tel}"}]},
        "request": {"url": f"https://x/api/{tel}/turnos?p={tel}"},
        "extra": {"lista": [f"a {tel}"], "n": 5},
    }
    s = json.dumps(sentry_before_send(evento))
    assert "5555" not in s and "4155552671" not in s
    assert "[filtrado]" in s


def test_no_filtra_numeros_que_no_son_telefonos():
    evento = {"message": "turno 123 en 2026-10-04T10:00:00 id 42", "extra": {"n": 12345}}
    out = sentry_before_send(evento)
    assert out["message"] == "turno 123 en 2026-10-04T10:00:00 id 42"
    assert out["extra"]["n"] == 12345


@pytest.mark.parametrize("campo", ["message", "msg", "args", "levelname", "name"])
def test_evento_obligatorio_rechaza_reservados_con_value_error(campo):
    with pytest.raises(ValueError):
        log_evento_obligatorio("tenant_purge_step", **{campo: "x"})


def test_limpiar_valor_con_anidamiento_extremo_no_levanta_recursion_error():
    from observabilidad.pii import MARCA_FILTRADO, limpiar_valor

    valor = "hoja"
    for _ in range(5000):
        valor = {"a": [valor]}
    resultado = limpiar_valor(valor)
    nivel, niveles = resultado, 0
    while isinstance(nivel, dict):
        nivel = nivel["a"][0]
        niveles += 1
    assert nivel == MARCA_FILTRADO
    assert niveles < 100


def test_limpiar_valor_con_referencia_circular_no_levanta_recursion_error():
    from observabilidad.pii import MARCA_FILTRADO, limpiar_valor

    circular = {"x": 1}
    circular["yo"] = circular
    lista = [1]
    lista.append(lista)
    lista.append(lista)
    assert limpiar_valor(circular) == {"x": 1, "yo": MARCA_FILTRADO}
    assert limpiar_valor(lista) == [1, MARCA_FILTRADO, MARCA_FILTRADO]


def test_limpiar_valor_por_debajo_del_tope_filtra_como_siempre():
    from observabilidad.pii import limpiar_valor

    assert limpiar_valor({"a": {"phone": "x", "b": ["+5491155550000", (1,)]}}) == {"a": {"b": ["[filtrado]", (1,)]}}
