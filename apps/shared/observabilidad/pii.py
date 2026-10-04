"""Campos considerados datos personales de end_customers (ARCHITECTURE.md §6.1 y §6.3)."""

PII_CAMPOS = frozenset(
    {
        "phone",
        "name",
        "channel_user_id",
        "text",
        "body",
        "message_text",
        "message_body",
        "content",
        "caption",
    }
)


def es_campo_pii(clave: object) -> bool:
    return isinstance(clave, str) and clave.lower() in PII_CAMPOS
