import { useCallback, useState, type FormEvent } from "react";
import { CLAVE_PERIODO_GRACIA, guardarSetting, listarSettings } from "../api/panel";
import { ConCarga } from "../compartido/ConCarga";
import { mensajeDeError, useCarga } from "../compartido/useCarga";

/** Claves editables (las mismas que valida el backend); el resto se muestra pero no se edita. */
const EDITABLES: Record<string, string> = {
  [CLAVE_PERIODO_GRACIA]: "Período de gracia de la baja de un tenant (días)",
};

export function Settings() {
  const cargar = useCallback(() => listarSettings(), []);
  const { carga } = useCarga(cargar);
  return (
    <section>
      <h1>Configuración de la plataforma</h1>
      <ConCarga
        carga={carga}
        hijos={(settings) => (
          <div className="panel">
            {Object.keys(settings).length === 0 && <p>No hay valores configurados.</p>}
            {Object.entries(settings).map(([clave, valor]) =>
              clave in EDITABLES ? (
                <EditorEntero key={clave} clave={clave} etiqueta={EDITABLES[clave] ?? clave} inicial={valor} />
              ) : (
                <p key={clave}>
                  <code>{clave}</code>: <code>{JSON.stringify(valor)}</code> (solo lectura)
                </p>
              ),
            )}
          </div>
        )}
      />
    </section>
  );
}

function EditorEntero({ clave, etiqueta, inicial }: { clave: string; etiqueta: string; inicial: unknown }) {
  const [texto, setTexto] = useState(String(inicial ?? ""));
  const [error, setError] = useState<string | null>(null);
  const [aviso, setAviso] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState(false);

  async function guardar(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setAviso(null);
    if (!/^[1-9]\d*$/.test(texto.trim())) {
      setError("Ingresá un número entero de días mayor que cero.");
      return;
    }
    setOcupado(true);
    try {
      const r = await guardarSetting(clave, Number(texto.trim()));
      setTexto(String(r.hasta));
      setAviso(`Guardado: ${String(r.desde ?? "sin valor")} → ${String(r.hasta)}.`);
    } catch (err) {
      setError(mensajeDeError(err));
    } finally {
      setOcupado(false);
    }
  }

  return (
    <form onSubmit={(e) => void guardar(e)}>
      <label className="campo">
        {etiqueta}
        <input inputMode="numeric" value={texto} onChange={(e) => setTexto(e.target.value)} />
      </label>
      <button type="submit" disabled={ocupado}>
        Guardar
      </button>
      {aviso && <p role="status">{aviso}</p>}
      {error && <p role="alert">{error}</p>}
    </form>
  );
}
