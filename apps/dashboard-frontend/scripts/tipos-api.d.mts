export const CONTRATO: string;
export const SALIDA: string;
export function generarTipos(ruta?: string): Promise<string>;
export function estaAlDia(contrato?: string, salida?: string): Promise<boolean>;
export const PERMISOS: string;
export const SALIDA_PERMISOS: string;
export function generarPermisos(ruta?: string): string;
export function permisosAlDia(catalogo?: string, salida?: string): boolean;
