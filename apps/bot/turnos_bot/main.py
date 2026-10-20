"""Entrypoint de producción: `uvicorn turnos_bot.main:app` (Render)."""

from turnos_bot.app import crear_app

app = crear_app()
