export interface Preferencia {
  recibe: boolean;
  email: boolean;
}

/**
 * Quien no recibe avisos queda sin canal (así es también el estado por defecto de quien no los recibe), y quien recibe
 * necesita al menos el email, el único canal disponible: una persona que recibe sin canal no recibiría nada y la
 * pantalla diría que se guardó. Por eso activar "Recibe avisos" activa el email, y quitar el email de quien recibe
 * lo deja sin recibir.
 */
export function conRecibe(recibe: boolean): Preferencia {
  return { recibe, email: recibe };
}

export function conEmail(actual: Preferencia, email: boolean): Preferencia {
  return email ? { ...actual, email: true } : { recibe: false, email: false };
}
