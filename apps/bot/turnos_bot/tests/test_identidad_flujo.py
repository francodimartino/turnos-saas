"""Flujos de identidad con un repositorio en memoria: alta, dedup, recontacto, "mis datos" y borrado.

Los teléfonos y nombres son inventados. La integración contra Postgres real está en
`test_identidad_integracion.py`.
"""

import uuid
from types import SimpleNamespace

import pytest
from platform_notifications import MensajeEntrante

from turnos_bot import ContextoMensaje
from turnos_bot.identidad import flujo, obtener_o_crear_end_customer
from turnos_bot.identidad.repositorio import EndCustomer
from turnos_bot.modelo_sesion import SesionBot

TENANT = uuid.uuid4()
USUARIO = "111"
TEL_CRUDO = "5491100000000"  # como lo manda Telegram: sin '+'
TEL = "+5491100000000"


class RepoFalso:
    """Reemplaza al módulo `repositorio`: guarda las filas y cuenta las operaciones."""

    def __init__(self) -> None:
        self.filas: dict[uuid.UUID, dict] = {}
        self.dormidos: list[uuid.UUID] = []
        self.llamadas: list[str] = []

    def agregar(self, **campos) -> uuid.UUID:
        id_ = uuid.uuid4()
        base = {"channel": None, "channel_user_id": None, "phone": None, "name": None, "extra_data": {}}
        self.filas[id_] = {**base, **campos}
        return id_

    def _a_cliente(self, id_: uuid.UUID) -> EndCustomer:
        f = self.filas[id_]
        return EndCustomer(id_, TENANT, f["channel"], f["channel_user_id"], f["phone"], f["name"], f["extra_data"])

    async def buscar_activo(self, conn, tenant_id, canal, usuario):
        for id_, f in self.filas.items():
            if f["channel"] == canal and f["channel_user_id"] == usuario and not f.get("deleted"):
                return self._a_cliente(id_)
        return None

    async def hay_perfil_dormido(self, conn, canal, usuario):
        return self.dormidos[-1] if self.dormidos else None

    async def crear(self, conn, tenant_id, canal, usuario, telefono=None):
        self.llamadas.append("crear")
        id_ = self.agregar(channel=canal, channel_user_id=usuario, phone=telefono)
        return self._a_cliente(id_)

    async def vincular_alta_manual(self, conn, tenant_id, canal, usuario, telefono):
        for id_, f in self.filas.items():
            if f["phone"] == telefono and f["channel"] is None:
                self.llamadas.append("vincular")
                f["channel"], f["channel_user_id"] = canal, usuario
                return self._a_cliente(id_)
        return None

    async def reactivar(self, conn, tenant_id, dormido_id, canal, usuario):
        self.llamadas.append("reactivar")
        self.filas[dormido_id].update(channel=canal, channel_user_id=usuario, deleted=False)
        self.dormidos.remove(dormido_id)
        return self._a_cliente(dormido_id)

    async def guardar_telefono(self, conn, tenant_id, id_, telefono):
        self.filas[id_]["phone"] = telefono

    async def guardar_nombre(self, conn, tenant_id, id_, nombre):
        self.filas[id_]["name"] = nombre

    async def anonimizar(self, conn, id_):
        self.llamadas.append("anonimizar")
        self.filas[id_].update(phone=None, name=None, extra_data={}, channel_user_id=None, deleted=True)


@pytest.fixture
def repo(monkeypatch):
    r = RepoFalso()
    for nombre in (
        "buscar_activo",
        "hay_perfil_dormido",
        "crear",
        "vincular_alta_manual",
        "reactivar",
        "guardar_telefono",
        "guardar_nombre",
        "anonimizar",
    ):
        monkeypatch.setattr(flujo.repositorio, nombre, getattr(r, nombre))
    return r


class Turno:
    """Una conversación: cada `decir(...)` es un mensaje que comparte la sesión con los anteriores."""

    def __init__(self) -> None:
        self.sesion = SesionBot()
        self.posteriores: list = []
        self.atendido = False

    async def decir(self, texto=None, *, callback=None, telefono=None) -> ContextoMensaje:
        mensaje = MensajeEntrante(
            "telegram", USUARIO, USUARIO, "u", texto=texto, callback_data=callback, telefono_compartido=telefono
        )
        ctx = ContextoMensaje(SimpleNamespace(), TENANT, mensaje, self.sesion.copia())
        self.atendido = await flujo.manejar_identidad(ctx)
        self.sesion = SesionBot() if ctx.descartar_sesion else ctx.sesion
        self.posteriores += ctx.posteriores
        return ctx


def _botones(ctx):
    return [b for r in ctx.respuestas for b in r.botones]


async def test_primer_contacto_pide_el_telefono_con_el_boton_de_contacto_y_no_crea_nada(repo):
    t = Turno()
    ctx = await t.decir("hola")
    assert t.atendido is True
    assert [b.pedir_contacto for b in _botones(ctx)] == [True]
    assert repo.filas == {}  # la fila se crea con el teléfono verificado
    assert t.sesion.identidad.paso == "telefono"


async def test_alta_nueva_normaliza_a_e164_y_pide_el_nombre(repo):
    t = Turno()
    await t.decir("hola")
    ctx = await t.decir(telefono=TEL_CRUDO)
    (fila,) = repo.filas.values()
    assert (fila["channel"], fila["channel_user_id"], fila["phone"]) == ("telegram", USUARIO, TEL)
    assert repo.llamadas == ["crear"]
    assert "llamás" in ctx.respuestas[0].texto
    ctx = await t.decir("  Ana   Prueba ")
    assert fila["name"] == "Ana Prueba"
    assert not t.sesion
    assert ctx.respuestas[0].texto == flujo.TEXTO_REGISTRADO


async def test_si_el_telefono_matchea_una_alta_manual_vincula_en_vez_de_crear(repo):
    manual = repo.agregar(phone=TEL, name="Cliente Manual")
    t = Turno()
    await t.decir("hola")
    ctx = await t.decir(telefono="+54 9 11 0000-0000")
    assert repo.llamadas == ["vincular"]  # no se creó otra fila
    assert len(repo.filas) == 1
    assert repo.filas[manual]["channel_user_id"] == USUARIO
    # ya tenía nombre: el alta termina sin volver a pedirlo
    assert ctx.respuestas[0].texto == flujo.TEXTO_REGISTRADO and not t.sesion


async def test_un_telefono_escrito_a_mano_nunca_vincula_ni_crea(repo):
    repo.agregar(phone=TEL, name="Otra Persona")
    t = Turno()
    await t.decir("hola")
    ctx = await t.decir("+54 9 11 0000 0000")  # no prueba que el número sea suyo
    assert repo.llamadas == []
    assert [b.pedir_contacto for b in _botones(ctx)] == [True]


async def test_telefono_ininterpretable_se_vuelve_a_pedir(repo):
    t = Turno()
    await t.decir("hola")
    ctx = await t.decir(telefono="123")
    assert ctx.respuestas[0].texto == flujo.TEXTO_TELEFONO_INVALIDO
    assert repo.filas == {} and t.sesion.identidad.paso == "telefono"


async def test_recontacto_pregunta_y_nunca_reactiva_sin_respuesta(repo):
    dormido = repo.agregar(channel="telegram", deleted=True)
    repo.dormidos.append(dormido)
    t = Turno()
    ctx = await t.decir("hola")
    assert [b.dato for b in _botones(ctx)] == [flujo.CB_REACTIVAR, flujo.CB_EMPEZAR_DE_CERO]
    ctx = await t.decir("sí, dale")  # texto libre: no es una respuesta
    assert [b.dato for b in _botones(ctx)] == [flujo.CB_REACTIVAR, flujo.CB_EMPEZAR_DE_CERO]
    assert repo.llamadas == []


async def test_reactivar_restaura_el_perfil_y_vuelve_a_pedir_telefono_y_nombre(repo):
    dormido = repo.agregar(channel="telegram", deleted=True)
    repo.dormidos.append(dormido)
    t = Turno()
    await t.decir("hola")
    ctx = await t.decir(callback=flujo.CB_REACTIVAR)
    assert repo.llamadas == ["reactivar"]
    assert repo.filas[dormido]["channel_user_id"] == USUARIO
    assert [b.pedir_contacto for b in _botones(ctx)] == [True]
    await t.decir(telefono=TEL_CRUDO)
    await t.decir("Ana Prueba")
    assert (repo.filas[dormido]["phone"], repo.filas[dormido]["name"]) == (TEL, "Ana Prueba")
    assert len(repo.filas) == 1 and "crear" not in repo.llamadas


async def test_reactivar_un_perfil_ya_reactivado_o_inexistente_reinicia_el_alta_sin_error(repo, monkeypatch):
    dormido = repo.agregar(channel="telegram", deleted=True)
    repo.dormidos.append(dormido)
    t = Turno()
    await t.decir("hola")

    async def ya_no_esta(conn, tenant_id, dormido_id, canal, usuario):
        raise LookupError("el perfil dormido ya no está disponible")

    monkeypatch.setattr(flujo.repositorio, "reactivar", ya_no_esta)
    ctx = await t.decir(callback=flujo.CB_REACTIVAR)  # no debe propagar el LookupError (sería un 500)
    assert [b.pedir_contacto for b in _botones(ctx)] == [True]
    assert t.sesion.identidad.paso == "telefono"
    await t.decir(telefono=TEL_CRUDO)
    assert repo.llamadas == ["crear"]  # el alta sigue como una nueva


async def test_empezar_de_cero_crea_una_fila_nueva_y_deja_la_vieja_dormida(repo):
    dormido = repo.agregar(channel="telegram", deleted=True)
    repo.dormidos.append(dormido)
    t = Turno()
    await t.decir("hola")
    await t.decir(callback=flujo.CB_EMPEZAR_DE_CERO)
    assert repo.llamadas == []
    await t.decir(telefono=TEL_CRUDO)
    assert repo.llamadas == ["crear"]
    assert repo.filas[dormido]["deleted"] is True and repo.filas[dormido]["channel_user_id"] is None


async def test_boton_viejo_de_reactivar_sin_paso_en_la_sesion_no_hace_nada(repo):
    dormido = repo.agregar(channel="telegram", deleted=True)
    repo.dormidos.append(dormido)
    t = Turno()
    ctx = await t.decir(callback=flujo.CB_REACTIVAR)
    assert "reactivar" not in repo.llamadas
    assert ctx.respuestas[0].texto == flujo.TEXTO_OPCION_VENCIDA


async def test_mis_datos_devuelve_solo_los_datos_propios(repo):
    repo.agregar(channel="telegram", channel_user_id="999", phone="+5491100000099", name="Otro Cliente")
    repo.agregar(
        channel="telegram", channel_user_id=USUARIO, phone=TEL, name="Ana Prueba", extra_data={"alergias": "ninguna"}
    )
    t = Turno()
    ctx = await t.decir("Mis datos")
    texto = ctx.respuestas[0].texto
    assert TEL in texto and "Ana Prueba" in texto and "alergias" in texto
    assert "Otro Cliente" not in texto and "00099" not in texto


async def test_mis_datos_sin_perfil_no_inventa_nada(repo):
    ctx = await Turno().decir("mis datos")
    assert ctx.respuestas[0].texto == flujo.TEXTO_SIN_DATOS


def _cliente_completo(repo):
    return repo.agregar(channel="telegram", channel_user_id=USUARIO, phone=TEL, name="Ana Prueba")


async def test_borrar_pide_confirmacion_y_no_borra_hasta_confirmar(repo):
    id_ = _cliente_completo(repo)
    t = Turno()
    ctx = await t.decir("borrar mis datos")
    assert [b.dato for b in _botones(ctx)] == [flujo.CB_BORRAR_SI, flujo.CB_BORRAR_NO]
    assert repo.llamadas == [] and repo.filas[id_]["phone"] == TEL
    await t.decir("quizás")  # no es una confirmación
    assert repo.llamadas == []
    ctx = await t.decir(callback=flujo.CB_BORRAR_NO)
    assert repo.llamadas == [] and ctx.respuestas[0].texto == flujo.TEXTO_BORRADO_CANCELADO


async def test_confirmar_anonimiza_agenda_la_limpieza_de_calendar_y_confirma_sin_datos(repo):
    id_ = _cliente_completo(repo)
    t = Turno()
    await t.decir("borrar mis datos")
    ctx = await t.decir(callback=flujo.CB_BORRAR_SI)
    assert repo.llamadas == ["anonimizar"] and repo.filas[id_]["phone"] is None
    assert len(t.posteriores) == 1  # la limpieza de Calendar corre después del commit
    assert ctx.descartar_sesion is True
    (respuesta,) = ctx.respuestas
    assert respuesta.texto == flujo.TEXTO_BORRADO
    assert "Ana" not in respuesta.texto and TEL not in respuesta.texto
    assert respuesta.end_customer_id is None  # no deja notificación ligada al cliente borrado


async def test_borrar_sin_perfil_no_hace_nada(repo):
    ctx = await Turno().decir("/borrarmisdatos")
    assert ctx.respuestas[0].texto == flujo.TEXTO_SIN_DATOS and repo.llamadas == []


async def test_cliente_completo_deja_pasar_los_mensajes_de_otros_flujos(repo):
    _cliente_completo(repo)
    t = Turno()
    ctx = await t.decir("quiero un turno")
    assert t.atendido is False and ctx.respuestas == []


async def test_cliente_con_alta_a_medias_retoma_donde_quedo(repo):
    repo.agregar(channel="telegram", channel_user_id=USUARIO, phone=TEL)  # sin nombre
    t = Turno()
    ctx = await t.decir("hola")
    assert t.atendido is True and "llamás" in ctx.respuestas[0].texto


async def test_obtener_o_crear_devuelve_el_cliente_completo_o_none_pidiendo_el_dato(repo):
    mensaje = MensajeEntrante("telegram", USUARIO, USUARIO, "u", texto="hola")
    ctx = ContextoMensaje(SimpleNamespace(), TENANT, mensaje)
    assert await obtener_o_crear_end_customer(ctx) is None
    assert ctx.respuestas and ctx.sesion.identidad.paso == "telefono"
    id_ = _cliente_completo(repo)
    ctx = ContextoMensaje(SimpleNamespace(), TENANT, mensaje)
    cliente = await obtener_o_crear_end_customer(ctx)
    assert cliente is not None and cliente.id == id_ and ctx.respuestas == []
