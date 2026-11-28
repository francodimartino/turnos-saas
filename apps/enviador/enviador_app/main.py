"""Entrypoint de producción: `uvicorn enviador_app.main:app` (un solo proceso, siempre activo)."""

from enviador_app.app import crear_app

app = crear_app()
