"""Slugs que un negocio no puede tomar (ARCHITECTURE.md §2.12).

`agendemos.ar/<slug>` comparte la raíz con la landing y los servicios propios: estos nombres quedan fuera. Es el
único lugar del código con la lista; la base la repite en `public.slug_reservado` (migración de slugs) y un test
las compara. Agregar un nombre exige una migración nueva que redefina esa función.
"""

SLUGS_RESERVADOS: frozenset[str] = frozenset(
    {
        "admin",
        "admin-api",
        "api",
        "app",
        "assets",
        "ayuda",
        "blog",
        "bot",
        "contacto",
        "cuenta",
        "dashboard",
        "docs",
        "health",
        "login",
        "logout",
        "panel",
        "precios",
        "privacidad",
        "registro",
        "reservas",
        "reservas-api",
        "soporte",
        "static",
        "status",
        "terminos",
        "webhook",
        "webhooks",
        "www",
    }
)


def es_slug_reservado(slug: str) -> bool:
    return slug in SLUGS_RESERVADOS
