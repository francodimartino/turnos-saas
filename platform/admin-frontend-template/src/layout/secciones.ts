export interface Seccion {
  ruta: string;
  etiqueta: string;
}

/** Registro de secciones del panel: las pantallas de administración se suman acá. */
export const SECCIONES: readonly Seccion[] = [{ ruta: "/", etiqueta: "Inicio" }];
