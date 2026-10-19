"""Error 404 de los routers y reexporte de `armar_set` (el armado de UPDATE parciales vive en `sql_parcial`)."""

from fastapi import HTTPException

from dashboard_backend.sql_parcial import armar_set

__all__ = ["armar_set", "no_encontrado"]


def no_encontrado(que: str) -> HTTPException:
    return HTTPException(status_code=404, detail={"codigo": "no_encontrado", "mensaje": f"{que} no existe."})
