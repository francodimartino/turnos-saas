"""Constructores de errores HTTP con la forma `{"detail": {"codigo", "mensaje"}}`."""

from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from platform_auth import ErrorAuth


def cuerpo_error(codigo: str, mensaje: str, **extra: Any) -> dict[str, Any]:
    """Cuerpo JSON de un error, tal como lo serializa FastAPI para una `HTTPException`."""
    return {"detail": {"codigo": codigo, "mensaje": mensaje, **extra}}


def error_http(estado: int, codigo: str, mensaje: str, **extra: Any) -> HTTPException:
    """`HTTPException` con `detail = {codigo, mensaje, **extra}`."""
    return HTTPException(status_code=estado, detail={"codigo": codigo, "mensaje": mensaje, **extra})


def no_encontrado(que: str) -> HTTPException:
    """404 `no_encontrado` con el mensaje «<que> no existe.»."""
    return error_http(404, "no_encontrado", f"{que} no existe.")


def respuesta_error(estado: int, codigo: str, mensaje: str, **extra: Any) -> JSONResponse:
    """Misma forma que `error_http`, para los handlers de excepción que devuelven la respuesta directamente."""
    return JSONResponse(status_code=estado, content=cuerpo_error(codigo, mensaje, **extra))


def http_desde_error_auth(error: ErrorAuth) -> HTTPException:
    """Traduce un `ErrorAuth` a su HTTP; el 401 anuncia el esquema `Bearer`."""
    headers = {"WWW-Authenticate": "Bearer"} if error.status_code == 401 else None
    exc = error_http(error.status_code, error.codigo, error.mensaje)
    exc.headers = headers
    return exc
