"""Consultas del cliente por el bot: mis próximos turnos, servicios y precios, datos del local, hablar con el negocio.

También es dueño del menú principal (`mostrar_menu_principal`), que es la respuesta por defecto del bot ante un
mensaje que ningún flujo atiende. Las claves de `tenants.settings` que lee están en `consultas/local.py`.
"""

from turnos_bot.consultas.flujo import CB_RESERVAR, manejar_consultas, mostrar_menu_principal

__all__ = ["CB_RESERVAR", "manejar_consultas", "mostrar_menu_principal"]
