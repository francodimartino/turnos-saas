export const CONTRATO: string;
export const SALIDA: string;
export function generarTipos(ruta?: string): Promise<string>;
export function estaAlDia(contrato?: string, salida?: string): Promise<boolean>;
