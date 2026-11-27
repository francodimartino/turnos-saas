import { useState, type FormEvent } from "react";
import { ErrorApi } from "../../api/cliente";
import {
  listarClientes,
  unirClientes,
  type Cliente,
  type ClienteListado,
  type ResultadoUnion,
} from "../../api/clientes";

interface Props {
  /** La ficha que queda. */
  cliente: Cliente;
  alUnir: (resultado: ResultadoUnion) => void;
}

/** Mensaje según el código del backend: nunca se muestra el texto que mande el servidor. */
export function mensajeDeUnion(e: unknown): string {
  if (!(e instanceof ErrorApi)) return "No se pudo unir las fichas. Probá de nuevo.";
  switch (e.codigo) {
    case "union_misma_ficha":
      return "No se puede unir una ficha consigo misma: elegí otra.";
    case "cliente_anonimizado":
      return "Una de las fichas ya fue anonimizada y no se puede unir.";
    case "canal_en_conflicto":
      return "Las dos fichas tienen una cuenta distinta del mismo canal (por ejemplo, dos cuentas de Telegram o dos emails verificados). No se pueden unir sin decidir antes cuál es de la persona.";
    case "cliente_no_encontrado":
      return "No encontramos una de las fichas. Es posible que ya no exista: actualizá la lista.";
    default:
      return e.estado === 403 ? "No tenés permiso para unir fichas." : "No se pudo unir las fichas. Probá de nuevo.";
  }
}

function nombre(c: Pick<ClienteListado, "name">): string {
  return c.name ?? "Sin nombre";
}

function datosDeContacto(c: Pick<ClienteListado, "phone" | "email">): string {
  return [c.phone, c.email].filter((x): x is string => x !== null && x !== "").join(" · ") || "sin teléfono ni email";
}

/** Unir dos fichas de la misma persona: busca la otra, explica qué ficha queda y pide confirmación expresa. */
export function UnirCliente({ cliente, alUnir }: Props) {
  const [abierto, setAbierto] = useState(false);
  const [escrito, setEscrito] = useState("");
  const [resultados, setResultados] = useState<ClienteListado[] | null>(null);
  const [elegido, setElegido] = useState<ClienteListado | null>(null);
  const [trabajando, setTrabajando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function cerrar() {
    setAbierto(false);
    setEscrito("");
    setResultados(null);
    setElegido(null);
    setError(null);
  }

  async function buscar(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setTrabajando(true);
    try {
      const { clientes } = await listarClientes(escrito, 0);
      setResultados(clientes.filter((c) => c.id !== cliente.id && !c.sin_cliente_identificable));
    } catch (err) {
      setResultados(null);
      setError(mensajeDeUnion(err));
    }
    setTrabajando(false);
  }

  async function confirmar(absorbida: ClienteListado) {
    setTrabajando(true);
    setError(null);
    try {
      const r = await unirClientes(cliente.id, absorbida.id);
      cerrar();
      alUnir(r);
    } catch (err) {
      setError(mensajeDeUnion(err));
    }
    setTrabajando(false);
  }

  if (!abierto) {
    return (
      <button type="button" onClick={() => setAbierto(true)}>
        Unir con otra ficha
      </button>
    );
  }

  if (elegido) {
    return (
      <div className="panel" role="group" aria-label="Confirmar unión de fichas">
        <h3>Unir fichas</h3>
        <p>
          <strong>Esta acción es irreversible.</strong>
        </p>
        <ul>
          <li>
            Queda la ficha de <strong>{nombre(cliente)}</strong> ({datosDeContacto(cliente)}).
          </li>
          <li>
            La ficha de <strong>{nombre(elegido)}</strong> ({datosDeContacto(elegido)}) se anonimiza: deja de poder
            identificarse a esa persona.
          </li>
          <li>Sus turnos, packs, canales y avisos pasan a la ficha que queda.</li>
          <li>
            La ficha que queda conserva su nombre, teléfono y email; solo completa los que le falten. Si las dos
            fichas tenían un dato distinto, el de la ficha anonimizada no se conserva.
          </li>
        </ul>
        {error && <p role="alert">{error}</p>}
        <div className="acciones">
          <button type="button" disabled={trabajando} onClick={() => void confirmar(elegido)}>
            Confirmar unión
          </button>
          <button type="button" onClick={cerrar}>
            Cancelar
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="panel" role="group" aria-label="Unir con otra ficha">
      <h3>Unir con otra ficha</h3>
      <p>Buscá la otra ficha de la misma persona. La ficha de {nombre(cliente)} es la que queda.</p>
      <form className="filtros" role="search" onSubmit={(e) => void buscar(e)}>
        <label>
          Buscar la otra ficha por nombre o teléfono
          <input value={escrito} maxLength={100} onChange={(e) => setEscrito(e.target.value)} />
        </label>
        <button type="submit" disabled={trabajando}>
          Buscar ficha
        </button>
      </form>
      {error && <p role="alert">{error}</p>}
      {resultados !== null &&
        (resultados.length === 0 ? (
          <p>No se encontraron otras fichas con esa búsqueda.</p>
        ) : (
          <ul>
            {resultados.map((c) => (
              <li key={c.id}>
                {nombre(c)} ({datosDeContacto(c)}){" "}
                <button type="button" onClick={() => setElegido(c)}>
                  Elegir a {nombre(c)}
                </button>
              </li>
            ))}
          </ul>
        ))}
      <button type="button" onClick={cerrar}>
        Cancelar
      </button>
    </div>
  );
}
