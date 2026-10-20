import { leerSettings } from "../api/catalogo";

export interface CampoExtra {
  key: string;
  label: string;
}

/** `end_customer_extra_fields` admite lista de claves o de `{key, label}`: se normaliza a la segunda forma. */
export function camposExtraDe(valor: unknown): CampoExtra[] {
  if (!Array.isArray(valor)) return [];
  const campos: CampoExtra[] = [];
  for (const item of valor) {
    if (typeof item === "string") campos.push({ key: item, label: item });
    else if (typeof item === "object" && item !== null && "key" in item && typeof item.key === "string") {
      const label = "label" in item && typeof item.label === "string" ? item.label : item.key;
      campos.push({ key: item.key, label });
    }
  }
  return campos;
}

/** Campos accesorios que el local configuró para sus clientes. Si no se pueden leer, no se ofrecen. */
export async function cargarCamposExtra(): Promise<CampoExtra[]> {
  try {
    const { settings } = await leerSettings();
    return camposExtraDe(settings["end_customer_extra_fields"]);
  } catch {
    return [];
  }
}
