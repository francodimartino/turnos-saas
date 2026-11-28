"""Quién del staff recibe los avisos al negocio y por qué canal (ARCHITECTURE.md §2.12).

  GET /local/avisos    manage_staff    staff activo con lo que rige para cada persona y los canales disponibles
  PUT /local/avisos    manage_staff    cambia a una o más personas (hasta 100 por pedido); devuelve el resultado

Body del PUT: `{"cambios": [{"staff_id", "por_defecto"?, "recibe_avisos"?, "canal_email"?, "canal_whatsapp"?}]}`.
Cada cambio es una de dos cosas: `por_defecto: true` (la persona vuelve a la regla por defecto: owner y quienes pueden
aprobar turnos, por email) o las dos preferencias `recibe_avisos` y `canal_email`. WhatsApp se muestra pero no se puede
activar (422 `canal_no_disponible`): todavía no hay envío por ese canal.

Un `PUT` que no cambia nada no escribe ni audita; uno que cambia algo deja una fila de `audit_log` con solo las
personas que cambiaron.

Errores: 422 `cambio_invalido`, `staff_repetido`, `staff_inexistente` (también para una persona de otro negocio),
`canal_no_disponible`; 409 `staff_inactivo` y `sin_destinatario_de_avisos` (el cambio dejaría al negocio sin nadie que
reciba los avisos por email: la base lo rechaza y no se escribe ni se audita nada); 409 `conflicto_de_concurrencia`
(reintentable) si Postgres abortó la transacción por un choque con otro cambio.

Este módulo solo traduce HTTP: las reglas, el SQL y la auditoría están en `avisos_negocio`, que no conoce FastAPI.
"""

from typing import Annotated, Any

from asyncpg.exceptions import DeadlockDetectedError, SerializationError
from fastapi import APIRouter, Depends
from platform_auth import Permiso
from turnos_http import error_http

from dashboard_backend import avisos_negocio
from dashboard_backend.avisos_negocio import (
    CambioAvisos,
    ErrorCambioInvalido,
    ErrorCanalNoDisponible,
    ErrorSinDestinatarioDeAvisos,
    ErrorStaffInactivo,
    ErrorStaffInexistente,
    ErrorStaffRepetido,
)
from dashboard_backend.dependencias import Transaccion, transaccion_con_permiso
from dashboard_backend.errores_db import traducir_errores_db
from dashboard_backend.esquemas_avisos import AvisosCuerpo
from dashboard_backend.respuestas_avisos import AvisosOut

router = APIRouter(tags=["local"])

# Con nombre para poder reemplazarla en `app.dependency_overrides`.
tx_gestiona_personal = transaccion_con_permiso(Permiso.GESTIONAR_PERSONAL)
GestionaPersonal = Annotated[Transaccion, Depends(tx_gestiona_personal)]

_MENSAJE_SIN_DESTINATARIO = (
    "Con este cambio el local se quedaría sin nadie que reciba los avisos por email. "
    "Antes elegí a otra persona para recibirlos."
)


@router.get("/local/avisos", response_model=AvisosOut)
async def leer_avisos(tx: GestionaPersonal) -> Any:
    return await avisos_negocio.listar_avisos(tx.conn, tx.sesion)


@router.put("/local/avisos", response_model=AvisosOut)
async def cambiar_avisos(datos: AvisosCuerpo, tx: GestionaPersonal) -> Any:
    cambios = [
        CambioAvisos(c.staff_id, c.por_defecto, c.recibe_avisos, c.canal_email, c.canal_whatsapp) for c in datos.cambios
    ]
    try:
        with traducir_errores_db():
            return await avisos_negocio.aplicar_cambios(tx.conn, tx.sesion, cambios)
    except ErrorCambioInvalido:
        raise error_http(
            422,
            "cambio_invalido",
            "Cada cambio es o bien volver al default (por_defecto) o bien recibe_avisos y canal_email.",
        ) from None
    except ErrorCanalNoDisponible:
        raise error_http(
            422, "canal_no_disponible", "El canal de WhatsApp todavía no está disponible para los avisos."
        ) from None
    except ErrorStaffRepetido:
        raise error_http(422, "staff_repetido", "Cada persona puede aparecer una sola vez en el pedido.") from None
    except ErrorStaffInexistente:
        raise error_http(
            422, "staff_inexistente", "Alguna de las personas indicadas no existe en el equipo de este local."
        ) from None
    except ErrorStaffInactivo:
        raise error_http(
            409, "staff_inactivo", "Alguna de las personas indicadas está desactivada: primero reactivala."
        ) from None
    except ErrorSinDestinatarioDeAvisos:
        raise error_http(409, "sin_destinatario_de_avisos", _MENSAJE_SIN_DESTINATARIO) from None
    except (DeadlockDetectedError, SerializationError):
        raise error_http(
            409,
            "conflicto_de_concurrencia",
            "Otro cambio se cruzó con este. Probá de nuevo.",
            reintentable=True,
        ) from None
