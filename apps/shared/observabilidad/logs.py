"""Logs estructurados JSON con tenant_id/actor_type (ARCHITECTURE.md §6.3)."""

import json
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import IO, Any

from .pii import es_campo_pii, limpiar_valor

EVENTOS_OBLIGATORIOS = frozenset({"anonymize_end_customer", "tenant_purge_step"})

_tenant_id: ContextVar[str | None] = ContextVar("tenant_id", default=None)
_actor_type: ContextVar[str | None] = ContextVar("actor_type", default=None)

_ESTANDAR = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime"}
_MARCA = "_observabilidad_handler"

# Lista blanca de campos `extra` que el formateador deja pasar: son los que el código del repo ya usa
# (un test recorre el repo y falla si aparece una clave fuera de esta lista). Todo lo demás se descarta.
CAMPOS_EXTRA_PERMITIDOS = frozenset(
    {
        "metodo", "ruta", "estado", "duracion_ms", "codigo", "error", "error_tipo", "motivo", "evento",
        "update_id", "respuestas", "sqlstate", "intento", "resultado", "profesional_id", "accion",
        "reintentable", "descripcion", "tenant_id", "actor_type", "variable", "purgados", "fallidos",
        "vencidos", "avisados", "sin_aviso", "omitidos", "fallos", "sesiones_borradas", "paso", "step", "count",
        "failed", "end_customer_id", "calendar_events", "calendar_not_scrubbed",
    }
)  # fmt: skip


@contextmanager
def contexto_log(tenant_id: str | None = None, actor_type: str | None = None) -> Iterator[None]:
    """Fija tenant_id/actor_type para los logs emitidos dentro del bloque."""
    t1 = _tenant_id.set(tenant_id) if tenant_id is not None else None
    t2 = _actor_type.set(actor_type) if actor_type is not None else None
    try:
        yield
    finally:
        if t2 is not None:
            _actor_type.reset(t2)
        if t1 is not None:
            _tenant_id.reset(t1)


class FormateadorJson(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        datos: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),  # noqa: UP017
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if (t := _tenant_id.get()) is not None:
            datos["tenant_id"] = t
        if (a := _actor_type.get()) is not None:
            datos["actor_type"] = a
        for clave, valor in record.__dict__.items():
            if clave in CAMPOS_EXTRA_PERMITIDOS and clave not in _ESTANDAR:
                datos[clave] = valor
        if record.exc_info:
            datos["exc_info"] = self.formatException(record.exc_info)
        # Filtro final sobre TODO registro: claves de PII (también anidadas) y teléfonos en textos.
        return json.dumps(limpiar_valor(datos), ensure_ascii=False, default=str)


def configurar_logging(nivel: int = logging.INFO, stream: IO[str] | None = None) -> None:
    """Deja el logger raíz emitiendo JSON por stdout (o `stream`). Idempotente."""
    raiz = logging.getLogger()
    for h in [h for h in raiz.handlers if getattr(h, _MARCA, False)]:
        raiz.removeHandler(h)
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(FormateadorJson())
    setattr(handler, _MARCA, True)
    raiz.addHandler(handler)
    raiz.setLevel(nivel)
    # httpx/httpcore escriben la URL completa (token del bot de Telegram) a nivel INFO/DEBUG.
    for nombre in ("httpx", "httpcore"):
        logging.getLogger(nombre).setLevel(logging.WARNING)


def log_evento_obligatorio(evento: str, **campos: Any) -> None:
    """Registra un evento obligatorio (respaldo de audit_log, §6.3/§6.5). Rechaza campos de PII."""
    if evento not in EVENTOS_OBLIGATORIOS:
        raise ValueError(f"Evento obligatorio desconocido: {evento!r}")
    reservados = sorted(c for c in campos if c in _ESTANDAR)
    if reservados:
        raise ValueError(f"Campos reservados de LogRecord no permitidos: {reservados}")
    prohibidos = sorted(c for c in campos if es_campo_pii(c))
    if prohibidos:
        raise ValueError(f"Campos de PII no permitidos en el log: {prohibidos}")
    logging.getLogger("observabilidad.eventos").info(evento, extra={"evento": evento, **campos})
