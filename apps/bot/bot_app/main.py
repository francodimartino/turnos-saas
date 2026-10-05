"""Entrypoint de producción: `uvicorn bot_app.main:app` (Render)."""

from bot_app.app import crear_app

app = crear_app()
