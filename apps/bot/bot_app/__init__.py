"""Bot de turnos (FastAPI): webhook de Telegram, deduplicación y sesiones (ARCHITECTURE.md §2.6).

- `webhook`: el pipeline de cada request, en el orden exacto de §2.6.
- `sesiones`: estado de conversación en `bot_sessions` (1 hora de inactividad).
- `despachador`: punto de extensión donde se enchufan los flujos de conversación.
- `app`: `crear_app` (rutas, `/health`, Sentry y logs).
"""

from bot_app.app import crear_app
from bot_app.despachador import ContextoMensaje, Despachador, Respuesta

__all__ = ["ContextoMensaje", "Despachador", "Respuesta", "crear_app"]
