"""Simulador local de Telegram: hace de Bot API (el bot le habla a él) y de "teléfono" de clientes ficticios.

Solo para desarrollo en esta máquina; no es parte de ningún servicio desplegado. Dos caras:

  * Bot API falsa: `POST /bot<token>/<método>` (sendMessage, answerCallbackQuery y cualquier otro método, que
    responde ok). El bot la usa cuando `TELEGRAM_API_BASE_URL` apunta acá. Lo que el bot "manda" se guarda por
    conversación (bot, usuario).
  * Cliente ficticio: la página `/` deja elegir un usuario, escribir, tocar botones y compartir el contacto. Cada
    acción se manda como un update al webhook REAL del bot (`<SIM_BOT_URL>/api/bot/telegram/<webhook_key>`) con la
    cabecera `X-Telegram-Bot-Api-Secret-Token` correcta.

Un "bot" del simulador es el cuarteto (nombre, token, webhook_key, webhook_secret) de la integración de Telegram de un
negocio: los datos de demo lo registran solos; para un negocio creado a mano se agrega desde la página.

Sin dependencias fuera de FastAPI y httpx (ya son del proyecto). Estado en `SIM_DATA_DIR/estado.json` (los secretos
son de prueba y viven solo en esta máquina).
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

CABECERA_SECRET = "X-Telegram-Bot-Api-Secret-Token"  # noqa: S105 - nombre de una cabecera HTTP, no un secreto
MAX_MENSAJES = 3000
USUARIOS_INICIALES = [
    {"id": 7101, "nombre": "Lucía Fernández", "telefono": "5491155550101"},
    {"id": 7102, "nombre": "Martín Rojas", "telefono": "5491155550102"},
    {"id": 7103, "nombre": "Sofía Benítez", "telefono": "5491155550103"},
]


def armar_update(
    tipo: str,
    usuario: dict[str, Any],
    update_id: int,
    *,
    texto: str = "",
    dato: str = "",
    mensaje_origen: int = 1,
    callback_id: str = "",
) -> dict[str, Any]:
    """Update de Telegram (forma mínima que entiende el bot) para una acción del usuario ficticio."""
    chat = {"id": usuario["id"], "type": "private"}
    autor = {"id": usuario["id"], "is_bot": False, "first_name": usuario["nombre"].split()[0]}
    if tipo == "texto":
        mensaje = {"message_id": update_id, "from": autor, "chat": chat, "text": texto}
        return {"update_id": update_id, "message": mensaje}
    if tipo == "contacto":
        contacto = {"phone_number": usuario["telefono"], "first_name": autor["first_name"], "user_id": usuario["id"]}
        return {
            "update_id": update_id,
            "message": {"message_id": update_id, "from": autor, "chat": chat, "contact": contacto},
        }
    if tipo == "boton":
        callback = {
            "id": callback_id or str(update_id),
            "from": autor,
            "data": dato,
            "message": {"message_id": mensaje_origen, "chat": chat},
        }
        return {"update_id": update_id, "callback_query": callback}
    raise ValueError(f"tipo de acción desconocido: {tipo}")


def teclado_de(reply_markup: Any) -> list[list[dict[str, Any]]]:
    """Botones de un `reply_markup` de Telegram, normalizados a filas de {texto, dato | contacto}."""
    filas: list[list[dict[str, Any]]] = []
    if not isinstance(reply_markup, dict):
        return filas
    for fila in reply_markup.get("inline_keyboard") or []:
        filas.append([{"texto": str(b.get("text", "")), "dato": str(b.get("callback_data", ""))} for b in fila])
    for fila in reply_markup.get("keyboard") or []:
        filas.append([{"texto": str(b.get("text", "")), "contacto": bool(b.get("request_contact"))} for b in fila if b])
    return filas


class Almacen:
    """Estado del simulador (bots, usuarios y mensajes), guardado en un JSON."""

    def __init__(self, ruta: Path) -> None:
        self._ruta = ruta
        self._lock = threading.Lock()
        self.bots: list[dict[str, Any]] = []
        self.usuarios: list[dict[str, Any]] = [dict(u) for u in USUARIOS_INICIALES]
        self.mensajes: list[dict[str, Any]] = []
        self._ultimo_update = int(time.time() * 1000)
        self._siguiente_mensaje = 1
        if ruta.exists():
            datos = json.loads(ruta.read_text(encoding="utf-8"))
            self.bots = datos.get("bots", [])
            self.usuarios = datos.get("usuarios", self.usuarios)
            self.mensajes = datos.get("mensajes", [])
            self._siguiente_mensaje = max((m["id"] for m in self.mensajes), default=0) + 1
            self._ultimo_update = max(self._ultimo_update, datos.get("ultimo_update", 0))

    def _guardar(self) -> None:
        self._ruta.parent.mkdir(parents=True, exist_ok=True)
        datos = {
            "bots": self.bots,
            "usuarios": self.usuarios,
            "mensajes": self.mensajes[-MAX_MENSAJES:],
            "ultimo_update": self._ultimo_update,
        }
        self._ruta.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")

    def nuevo_update_id(self) -> int:
        with self._lock:
            self._ultimo_update += 1
            return self._ultimo_update

    def registrar_bot(self, nombre: str, token: str, webhook_key: str, webhook_secret: str) -> dict[str, Any]:
        with self._lock:
            existente = next((b for b in self.bots if b["webhook_key"] == webhook_key and webhook_key), None)
            datos = {"nombre": nombre, "token": token, "webhook_key": webhook_key, "webhook_secret": webhook_secret}
            if existente:
                existente.update(datos)
                bot = existente
            else:
                bot = {"id": max((b["id"] for b in self.bots), default=0) + 1, **datos}
                self.bots.append(bot)
            self._guardar()
            return bot

    def bot_por_token(self, token: str) -> dict[str, Any] | None:
        return next((b for b in self.bots if b["token"] == token), None)

    def agregar_usuario(self, nombre: str, telefono: str) -> dict[str, Any]:
        with self._lock:
            usuario = {
                "id": max((u["id"] for u in self.usuarios), default=7100) + 1,
                "nombre": nombre,
                "telefono": telefono,
            }
            self.usuarios.append(usuario)
            self._guardar()
            return usuario

    def agregar_mensaje(self, bot_id: int, usuario_id: int, de: str, texto: str, botones: list | None = None) -> dict:
        with self._lock:
            mensaje = {
                "id": self._siguiente_mensaje,
                "bot": bot_id,
                "usuario": usuario_id,
                "de": de,  # "bot" | "usuario"
                "texto": texto,
                "botones": botones or [],
                "hora": time.strftime("%H:%M:%S"),
            }
            self._siguiente_mensaje += 1
            self.mensajes.append(mensaje)
            self._guardar()
            return mensaje

    def conversacion(self, bot_id: int, usuario_id: int) -> list[dict[str, Any]]:
        return [m for m in self.mensajes if m["bot"] == bot_id and m["usuario"] == usuario_id]


class BotIn(BaseModel):
    nombre: str
    token: str
    webhook_key: str
    webhook_secret: str


class UsuarioIn(BaseModel):
    nombre: str
    telefono: str


class AccionIn(BaseModel):
    bot: int
    usuario: int
    tipo: str  # texto | boton | contacto
    texto: str = ""
    dato: str = ""
    mensaje_origen: int = 1


def crear_app(
    almacen: Almacen | None = None, bot_url: str | None = None, cliente: httpx.Client | None = None
) -> FastAPI:
    almacen = almacen or Almacen(Path(os.environ.get("SIM_DATA_DIR", "./data")) / "estado.json")
    bot_url = (bot_url or os.environ.get("SIM_BOT_URL", "http://localhost:8110")).rstrip("/")
    app = FastAPI(title="Simulador de Telegram (local)", docs_url=None, redoc_url=None)
    app.state.almacen = almacen
    pagina = Path(__file__).parent / "index.html"

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/")
    def inicio() -> FileResponse:
        return FileResponse(pagina, media_type="text/html; charset=utf-8")

    # --- Bot API falsa (la usa el bot) ------------------------------------------------------------------------------

    @app.post("/bot{token}/{metodo}")
    async def bot_api(token: str, metodo: str, cuerpo: dict[str, Any] | None = None) -> dict[str, Any]:
        cuerpo = cuerpo or {}
        if metodo == "sendMessage":
            bot = almacen.bot_por_token(token)
            if bot is None:  # token de un negocio que todavía no se registró en la página
                bot = almacen.registrar_bot(f"Bot sin registrar ({token[:6]}…)", token, "", "")
            try:
                chat = int(cuerpo.get("chat_id"))  # type: ignore[arg-type]  # un chat_id ausente (None) levanta TypeError y se captura abajo
            except (TypeError, ValueError):
                return JSONResponse({"ok": False, "error_code": 400, "description": "chat_id inválido"}, 400)  # type: ignore[return-value]  # JSONResponse es una Response válida para FastAPI
            m = almacen.agregar_mensaje(
                bot["id"], chat, "bot", str(cuerpo.get("text", "")), teclado_de(cuerpo.get("reply_markup"))
            )
            return {"ok": True, "result": {"message_id": m["id"], "chat": {"id": chat}}}
        return {"ok": True, "result": True}

    # --- Página (cliente ficticio) ------------------------------------------------------------------------------------

    @app.get("/api/estado")
    def estado() -> dict[str, Any]:
        bots = [
            {"id": b["id"], "nombre": b["nombre"], "enviable": bool(b["webhook_key"] and b["webhook_secret"])}
            for b in almacen.bots
        ]
        return {"bots": bots, "usuarios": almacen.usuarios, "bot_url": bot_url}

    @app.post("/api/bots")
    def agregar_bot(datos: BotIn) -> dict[str, Any]:
        bot = almacen.registrar_bot(
            datos.nombre.strip(), datos.token.strip(), datos.webhook_key.strip(), datos.webhook_secret
        )
        return {"id": bot["id"]}

    @app.post("/api/usuarios")
    def agregar_usuario(datos: UsuarioIn) -> dict[str, Any]:
        if not datos.nombre.strip():
            raise HTTPException(422, "El nombre es obligatorio")
        return almacen.agregar_usuario(datos.nombre.strip(), "".join(c for c in datos.telefono if c.isdigit()))

    @app.get("/api/conversacion")
    def conversacion(bot: int, usuario: int) -> list[dict[str, Any]]:
        return almacen.conversacion(bot, usuario)

    @app.post("/api/enviar")
    def enviar(a: AccionIn) -> dict[str, Any]:
        bot = next((b for b in almacen.bots if b["id"] == a.bot), None)
        usuario = next((u for u in almacen.usuarios if u["id"] == a.usuario), None)
        if bot is None or usuario is None:
            raise HTTPException(404, "Bot o usuario desconocido")
        if not (bot["webhook_key"] and bot["webhook_secret"]):
            raise HTTPException(422, "Ese bot no tiene webhook_key ni secret: registralo desde la página")
        try:
            update = armar_update(
                a.tipo, usuario, almacen.nuevo_update_id(), texto=a.texto, dato=a.dato, mensaje_origen=a.mensaje_origen
            )
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        etiqueta = {"texto": a.texto, "boton": f"[botón] {a.texto or a.dato}", "contacto": "[compartió su contacto]"}[
            a.tipo
        ]
        almacen.agregar_mensaje(bot["id"], usuario["id"], "usuario", etiqueta)
        url = f"{bot_url}/api/bot/telegram/{bot['webhook_key']}"
        try:
            if cliente is not None:
                r = cliente.post(url, json=update, headers={CABECERA_SECRET: bot["webhook_secret"]})
            else:
                with httpx.Client(timeout=15) as c:
                    r = c.post(url, json=update, headers={CABECERA_SECRET: bot["webhook_secret"]})
        except httpx.HTTPError as e:
            raise HTTPException(502, f"No se pudo llegar al bot ({type(e).__name__})") from None
        return {"estado": r.status_code}

    return app


app = crear_app() if os.environ.get("SIM_DATA_DIR") else None  # `uvicorn main:app` (en los tests se usa crear_app)
