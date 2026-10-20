import { listarProfesionales, listarServicios, serviciosDeProfesional, type Profesional, type Servicio } from "../../api/catalogo";
import { listarClientes, type Cliente } from "../../api/turnos";

/** Datos de referencia para los selectores y para poner nombres a los packs. */
export interface Base {
  profesionales: Profesional[];
  servicios: Servicio[];
  clientes: Cliente[];
  /** Servicios que ofrece cada profesional activo (por id de profesional). */
  ofrece: Record<string, string[]>;
}

export async function cargarBase(): Promise<Base> {
  const [profesionales, servicios, clientes] = await Promise.all([
    listarProfesionales(),
    listarServicios(),
    listarClientes(),
  ]);
  const pares = await Promise.all(
    profesionales
      .filter((p) => p.is_active)
      .map(async (p) => [p.id, (await serviciosDeProfesional(p.id)).map((s) => s.service_id)] as const),
  );
  return { profesionales, servicios, clientes, ofrece: Object.fromEntries(pares) };
}

export function nombreCliente(c: Cliente): string {
  if (c.sin_cliente_identificable) return "sin cliente identificable";
  return [c.name ?? "Sin nombre", c.phone ? `(${c.phone})` : null].filter(Boolean).join(" ");
}
