"""Interfaz de secrets que ven las apps (§1.6): nada acá menciona Supabase, Vault ni SQL."""

import uuid
from abc import ABC, abstractmethod
from typing import Any


class Secreto:
    """Valor sensible opaco: `repr`, `str`, f-strings, logs y pickle nunca muestran el valor.

    El valor solo sale con `revelar()`, para usarlo en el momento (llamar a la API del proveedor) y no guardarlo.
    """

    __slots__ = ("_valor",)

    def __init__(self, valor: str) -> None:
        if not isinstance(valor, str):
            raise TypeError("el valor de un Secreto debe ser texto")
        object.__setattr__(self, "_valor", valor)

    def revelar(self) -> str:
        return self._valor

    def __setattr__(self, nombre: str, valor: Any) -> None:
        raise AttributeError("Secreto es inmutable")

    def __repr__(self) -> str:
        return "Secreto(***)"

    __str__ = __repr__

    def __reduce_ex__(self, protocolo: Any) -> Any:
        raise TypeError("Secreto no se puede serializar")

    def __reduce__(self) -> Any:
        raise TypeError("Secreto no se puede serializar")


class AlmacenSecretos(ABC):
    """Secrets de integraciones (`tenant_integrations`). Opera dentro de la transacción de la request."""

    @abstractmethod
    async def obtener(self, integration_id: uuid.UUID) -> Secreto | None:
        """Secret de una integración del tenant del contexto; `None` si no tiene.

        Levanta `AccesoSecretoDenegado` si no existe o es de otro tenant.
        """

    @abstractmethod
    async def buscar(self, integration_type: str, professional_id: uuid.UUID | None = None) -> uuid.UUID | None:
        """Id de la integración del tenant del contexto (por tipo y, si aplica, profesional)."""

    @abstractmethod
    async def guardar(
        self, tenant_id: uuid.UUID, integration_type: str, config: dict[str, Any] | None, secret: "str | Secreto"
    ) -> uuid.UUID:
        """Alta o actualización de una integración de tenant. Solo `platform_admin`."""

    @abstractmethod
    async def guardar_profesional(
        self,
        professional_id: uuid.UUID,
        config: dict[str, Any] | None,
        secret: "str | Secreto",
        integration_type: str = "google_calendar",
    ) -> uuid.UUID:
        """Alta o actualización de la integración por-profesional del tenant del contexto."""

    @abstractmethod
    async def eliminar_profesional(self, professional_id: uuid.UUID) -> bool:
        """Borra la integración (y su secret) de un profesional; `False` si no había."""
