import { ErrorApi } from "../../api/cliente";

/**
 * Mensajes de cada acción de la pantalla, armados solo con el código (y el estado) del error: nunca el texto del
 * backend ni lo que ingresó la persona.
 */

const SIN_CONEXION = "No se pudo conectar con el servidor. Revisá tu conexión y probá de nuevo.";

export function mensajeDeSubirLogo(e: unknown): string {
  if (!(e instanceof ErrorApi)) return "No se pudo subir el logo. Probá de nuevo.";
  switch (e.codigo) {
    case "sin_conexion":
      return SIN_CONEXION;
    case "logo_tipo_invalido":
      return "El servidor no reconoció la imagen: tiene que ser un PNG, JPG o WebP válido.";
    case "logo_invalido":
      return "El servidor no pudo procesar la imagen. Probá con otro archivo.";
    case "logo_demasiado_grande":
    case "cuerpo_demasiado_grande":
      return "El servidor rechazó el logo por pesar más de 1 MB.";
    case "almacenamiento_no_disponible":
      return "El almacenamiento de imágenes no responde ahora. Probá de nuevo en unos minutos.";
    case "settings_demasiado_grandes":
      return "No hay lugar para guardar el logo entre los datos del negocio. Contactá a soporte.";
    case "no_encontrado":
      return "No se encontró el negocio al subir el logo. Volvé a iniciar sesión.";
    case "permiso_denegado":
      return "Tu usuario no tiene permiso para cambiar el logo.";
  }
  if (e.estado === 403) return "Tu usuario no tiene permiso para cambiar el logo.";
  return "No se pudo subir el logo. Probá de nuevo.";
}

export function mensajeDeQuitarLogo(e: unknown): string {
  if (!(e instanceof ErrorApi)) return "No se pudo quitar el logo. Probá de nuevo.";
  switch (e.codigo) {
    case "sin_conexion":
      return SIN_CONEXION;
    case "almacenamiento_no_disponible":
      return "El almacenamiento de imágenes no responde ahora, así que el logo sigue puesto. Probá de nuevo en unos minutos.";
    case "no_encontrado":
      return "No se encontró el negocio al quitar el logo. Volvé a iniciar sesión.";
    case "permiso_denegado":
      return "Tu usuario no tiene permiso para quitar el logo.";
  }
  if (e.estado === 403) return "Tu usuario no tiene permiso para quitar el logo.";
  return "No se pudo quitar el logo. Probá de nuevo.";
}

export function mensajeDeGuardarApariencia(e: unknown): string {
  if (!(e instanceof ErrorApi)) return "No se pudieron guardar los colores y el plazo. Probá de nuevo.";
  switch (e.codigo) {
    case "sin_conexion":
      return SIN_CONEXION;
    case "apariencia_invalida":
    case "datos_invalidos":
      return "El servidor rechazó los valores: los colores van como #RRGGBB y el plazo es un entero de 0 a 720 horas.";
    case "settings_demasiado_grandes":
      return "Los datos del negocio superan el tamaño permitido: no se pudieron guardar los colores ni el plazo.";
    case "no_encontrado":
      return "No se encontró el negocio al guardar. Volvé a iniciar sesión.";
    case "permiso_denegado":
      return "Tu usuario no tiene permiso para cambiar la apariencia ni el plazo.";
  }
  if (e.estado === 403) return "Tu usuario no tiene permiso para cambiar la apariencia ni el plazo.";
  return "No se pudieron guardar los colores y el plazo. Probá de nuevo.";
}
