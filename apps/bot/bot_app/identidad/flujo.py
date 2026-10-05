"""Identidad del cliente final en el bot (ARCHITECTURE.md §2.3, §2.8): alta, recontacto, "mis datos" y borrado.

Dos puntos de entrada:

- `manejar_identidad`: handler de la cadena del `Despachador`, va primero. Atiende los pasos de identidad en
  curso, los pedidos "mis datos" y "borrar mis datos", y completa el alta de quien todavía no la terminó.
  Devuelve `False` solo cuando el cliente ya está identificado y el mensaje es de otro flujo.
- `obtener_o_crear_end_customer(ctx)`: para los flujos de reserva y consultas (tareas 35 y 36). Devuelve el
  `EndCustomer` completo del mensaje o `None` si todavía hay que terminar el alta: en ese caso ya pidió el dato
  que falta (con `ctx.responder`) y el flujo que llama debe cortar con `return True`.

El estado de un alta o de un borrado a medias vive en `ctx.sesion["identidad"]` (`bot_sessions`, 1 hora).
El teléfono solo se acepta por el botón de compartir contacto de Telegram (el adapter descarta los contactos
ajenos): un número escrito a mano no prueba que sea del remitente, y vincularlo a una alta manual le daría el
perfil de otra persona a quien conozca el número.
"""

import json
import unicodedata
import uuid

from platform_notifications import Boton

from bot_app.despachador import ContextoMensaje
from bot_app.identidad import repositorio
from bot_app.identidad.calendario import limpiar_calendario_de
from bot_app.identidad.repositorio import EndCustomer
from bot_app.identidad.telefono import normalizar_e164

CLAVE = "identidad"

PASO_RECONTACTO = "recontacto"
PASO_TELEFONO = "telefono"
PASO_NOMBRE = "nombre"
PASO_CONFIRMAR_BORRADO = "confirmar_borrado"

CB_REACTIVAR = "idn:reactivar"
CB_EMPEZAR_DE_CERO = "idn:cero"
CB_BORRAR_SI = "idn:borrar_si"
CB_BORRAR_NO = "idn:borrar_no"
PREFIJO_CB = "idn:"

LARGO_MAXIMO_NOMBRE = 80

TEXTO_PEDIR_TELEFONO = "Para continuar necesito tu teléfono. Tocá el botón para compartir tu contacto."
TEXTO_TELEFONO_INVALIDO = "No pude interpretar ese número. Probá de nuevo con el botón de compartir contacto."
TEXTO_PEDIR_NOMBRE = "Gracias. ¿Cómo te llamás?"
TEXTO_REGISTRADO = "Listo, ya quedaste registrado."
TEXTO_RECONTACTO = (
    "Ya habías usado este servicio y pediste borrar tus datos. ¿Querés reactivar tu perfil anterior o empezar de cero?"
)
TEXTO_CONFIRMAR_BORRADO = (
    "Vas a borrar tus datos personales (teléfono, nombre y datos adicionales) y se cancelan tus turnos "
    "pendientes. No se puede deshacer. ¿Confirmás?"
)
TEXTO_BORRADO = "Listo: borré tus datos personales. Si volvés a escribir, vas a poder empezar de cero."
TEXTO_BORRADO_CANCELADO = "No borré nada."
TEXTO_SIN_DATOS = "No tengo datos tuyos guardados."
TEXTO_OPCION_VENCIDA = "Esa opción ya no está disponible."

_MIS_DATOS = {"mis datos", "/misdatos", "/mis_datos"}
_BORRAR = {"borrar mis datos", "/borrarmisdatos", "/borrar_mis_datos"}
_SI = {"si", "confirmo", "confirmar"}
_NO = {"no", "cancelar"}


def _normalizar(texto: str | None) -> str:
    if not texto:
        return ""
    sin_acentos = "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")
    return " ".join(sin_acentos.lower().split())


def _botones_recontacto() -> list[Boton]:
    return [Boton("Reactivar mi perfil anterior", CB_REACTIVAR), Boton("Empezar de cero", CB_EMPEZAR_DE_CERO)]


def _pedir_telefono(ctx: ContextoMensaje, texto: str = TEXTO_PEDIR_TELEFONO) -> None:
    ctx.responder(texto, [Boton("Compartir mi contacto", "idn:contacto", pedir_contacto=True)])


def _pedir_confirmacion_borrado(ctx: ContextoMensaje) -> None:
    ctx.responder(
        TEXTO_CONFIRMAR_BORRADO,
        [Boton("Sí, borrar mis datos", CB_BORRAR_SI), Boton("Cancelar", CB_BORRAR_NO)],
    )


def _paso(ctx: ContextoMensaje) -> str | None:
    estado = ctx.sesion.get(CLAVE)
    return estado.get("paso") if isinstance(estado, dict) else None


def _fijar_paso(ctx: ContextoMensaje, paso: str, **extra: str) -> None:
    ctx.sesion[CLAVE] = {"paso": paso, **extra}


def _cerrar_paso(ctx: ContextoMensaje) -> None:
    ctx.sesion.pop(CLAVE, None)


async def _cliente_activo(ctx: ContextoMensaje) -> EndCustomer | None:
    m = ctx.mensaje
    return await repositorio.buscar_activo(ctx.conn, ctx.tenant_id, m.canal, m.usuario_id)


async def _iniciar(ctx: ContextoMensaje, cliente: EndCustomer | None) -> None:
    """Arranca (o retoma) el alta según lo que falte; pregunta antes de reactivar un perfil dormido."""
    m = ctx.mensaje
    if cliente is None:
        dormido = await repositorio.hay_perfil_dormido(ctx.conn, m.canal, m.usuario_id)
        if dormido is not None:
            _fijar_paso(ctx, PASO_RECONTACTO, dormido_id=str(dormido))
            ctx.responder(TEXTO_RECONTACTO, _botones_recontacto())
            return
        _fijar_paso(ctx, PASO_TELEFONO)
        _pedir_telefono(ctx)
    elif not cliente.phone:
        _fijar_paso(ctx, PASO_TELEFONO)
        _pedir_telefono(ctx)
    else:
        _fijar_paso(ctx, PASO_NOMBRE)
        ctx.responder(TEXTO_PEDIR_NOMBRE)


async def obtener_o_crear_end_customer(ctx: ContextoMensaje) -> EndCustomer | None:
    """El `end_customer` del mensaje, completo (con teléfono y nombre), o `None` si el alta sigue en curso.

    Con `None` ya se pidió el dato que falta (teléfono, nombre, o la decisión de reactivar un perfil
    anterior) y la sesión quedó en ese paso: quien llama corta con `return True`. La fila se crea cuando llega
    el teléfono verificado, para poder vincularla a una alta manual del mismo número (§2.8).
    """
    cliente = await _cliente_activo(ctx)
    if cliente is not None and cliente.completo:
        return cliente
    await _iniciar(ctx, cliente)
    return None


async def _paso_recontacto(ctx: ContextoMensaje) -> None:
    m = ctx.mensaje
    estado = ctx.sesion[CLAVE]
    if m.callback_data == CB_REACTIVAR:
        cliente = await repositorio.reactivar(
            ctx.conn, ctx.tenant_id, uuid.UUID(estado["dormido_id"]), m.canal, m.usuario_id
        )
        # La anonimización borró teléfono y nombre: se vuelven a pedir (§2.3).
        await _iniciar(ctx, cliente)
    elif m.callback_data == CB_EMPEZAR_DE_CERO:
        _fijar_paso(ctx, PASO_TELEFONO)
        _pedir_telefono(ctx)
    else:
        # Sin respuesta explícita no se reactiva nada.
        ctx.responder(TEXTO_RECONTACTO, _botones_recontacto())


async def _paso_telefono(ctx: ContextoMensaje) -> None:
    m = ctx.mensaje
    if m.telefono_compartido is None:
        _pedir_telefono(ctx)
        return
    telefono = normalizar_e164(m.telefono_compartido)
    if telefono is None:
        _pedir_telefono(ctx, TEXTO_TELEFONO_INVALIDO)
        return
    cliente = await _cliente_activo(ctx)
    if cliente is None:
        cliente = await repositorio.vincular_alta_manual(ctx.conn, ctx.tenant_id, m.canal, m.usuario_id, telefono)
        if cliente is None:
            cliente = await repositorio.crear(ctx.conn, ctx.tenant_id, m.canal, m.usuario_id, telefono)
    else:
        await repositorio.guardar_telefono(ctx.conn, ctx.tenant_id, cliente.id, telefono)
    if cliente.name:
        _cerrar_paso(ctx)
        ctx.responder(TEXTO_REGISTRADO)
    else:
        _fijar_paso(ctx, PASO_NOMBRE)
        ctx.responder(TEXTO_PEDIR_NOMBRE)


async def _paso_nombre(ctx: ContextoMensaje) -> None:
    nombre = " ".join((ctx.mensaje.texto or "").split())
    cliente = await _cliente_activo(ctx)
    if not nombre or cliente is None:
        ctx.responder(TEXTO_PEDIR_NOMBRE)
        return
    await repositorio.guardar_nombre(ctx.conn, ctx.tenant_id, cliente.id, nombre[:LARGO_MAXIMO_NOMBRE])
    _cerrar_paso(ctx)
    ctx.responder(TEXTO_REGISTRADO)


async def _paso_confirmar_borrado(ctx: ContextoMensaje) -> None:
    m = ctx.mensaje
    texto = _normalizar(m.texto)
    if m.callback_data == CB_BORRAR_SI or texto in _SI:
        cliente = await _cliente_activo(ctx)
        if cliente is None:
            _cerrar_paso(ctx)
            ctx.responder(TEXTO_SIN_DATOS)
            return
        await repositorio.anonimizar(ctx.conn, cliente.id)
        # Lo de Google Calendar no se puede revertir: recién después del commit de la anonimización (§3.5).
        ctx.despues_del_commit(limpiar_calendario_de(ctx.tenant_id, cliente.id))
        ctx.terminar_sesion()
        ctx.responder(TEXTO_BORRADO)  # sin end_customer_id: no deja fila de notificación ligada al cliente
    elif m.callback_data == CB_BORRAR_NO or texto in _NO:
        _cerrar_paso(ctx)
        ctx.responder(TEXTO_BORRADO_CANCELADO)
    else:
        _pedir_confirmacion_borrado(ctx)


def _formatear_mis_datos(cliente: EndCustomer) -> str:
    extra = json.dumps(cliente.extra_data, ensure_ascii=False) if cliente.extra_data else "ninguno"
    return (
        "Estos son tus datos:\n"
        f"Teléfono: {cliente.phone or 'sin cargar'}\n"
        f"Nombre: {cliente.name or 'sin cargar'}\n"
        f"Datos adicionales: {extra}"
    )


async def manejar_identidad(ctx: ContextoMensaje) -> bool:
    """Handler de identidad (ver el docstring del módulo). `True` si atendió el mensaje."""
    m = ctx.mensaje
    texto = _normalizar(m.texto)
    cliente = await _cliente_activo(ctx)

    if texto in _MIS_DATOS:
        ctx.responder(TEXTO_SIN_DATOS if cliente is None else _formatear_mis_datos(cliente))
        return True
    if texto in _BORRAR:
        if cliente is None:
            ctx.responder(TEXTO_SIN_DATOS)
        else:
            _fijar_paso(ctx, PASO_CONFIRMAR_BORRADO)
            _pedir_confirmacion_borrado(ctx)
        return True

    paso = _paso(ctx)
    if paso == PASO_RECONTACTO:
        await _paso_recontacto(ctx)
        return True
    if paso == PASO_TELEFONO:
        await _paso_telefono(ctx)
        return True
    if paso == PASO_NOMBRE:
        await _paso_nombre(ctx)
        return True
    if paso == PASO_CONFIRMAR_BORRADO:
        await _paso_confirmar_borrado(ctx)
        return True

    if m.callback_data is not None and m.callback_data.startswith(PREFIJO_CB):
        # Botón de un paso que ya no existe (sesión vencida o mensaje viejo): nunca ejecuta nada.
        ctx.responder(TEXTO_OPCION_VENCIDA)
        return True
    if cliente is None or not cliente.completo:
        await _iniciar(ctx, cliente)
        return True
    return False
