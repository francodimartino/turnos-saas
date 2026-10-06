import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { ErrorApi } from "../../api/cliente";
import { crearCliente, modificarCliente, type Cliente } from "../../api/clientes";
import { Campo } from "../../shared/Campo";
import type { CampoExtra } from "../../shared/camposExtra";
import { erroresDeApi, SIN_ERRORES, type ErroresFormulario } from "../../shared/errores";

interface Props {
  /** `null`: alta manual. */
  inicial: Cliente | null;
  camposExtra: readonly CampoExtra[];
  alGuardar: () => void;
  alCancelar: () => void;
  /** Se llama al seguir el enlace al cliente que ya existía (para cerrar este formulario). */
  alVerExistente: () => void;
}

function valorExtra(cliente: Cliente | null, clave: string): string {
  const v = cliente?.extra_data?.[clave];
  return v === undefined || v === null ? "" : String(v);
}

function soloTexto(extra: Record<string, unknown> | undefined): Record<string, string> {
  const salida: Record<string, string> = {};
  for (const [k, v] of Object.entries(extra ?? {})) if (typeof v === "string" && v !== "") salida[k] = v;
  return salida;
}

export function FormularioCliente({ inicial, camposExtra, alGuardar, alCancelar, alVerExistente }: Props) {
  const [telefono, setTelefono] = useState(inicial?.phone ?? "");
  const [nombre, setNombre] = useState(inicial?.name ?? "");
  const [extras, setExtras] = useState<Record<string, string>>(
    Object.fromEntries(camposExtra.map((c) => [c.key, valorExtra(inicial, c.key)])),
  );
  const [errores, setErrores] = useState<ErroresFormulario>(SIN_ERRORES);
  const [duplicado, setDuplicado] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);

  async function enviar(e: FormEvent) {
    e.preventDefault();
    setDuplicado(null);
    const campos: Record<string, string> = {};
    if (telefono.trim() === "") campos["phone"] = "Ingresá el teléfono del cliente";
    if (nombre.trim() === "") campos["name"] = "Ingresá el nombre del cliente";
    if (Object.keys(campos).length > 0) {
      setErrores({ campos, general: null });
      return;
    }
    const extra: Record<string, string> = {};
    for (const c of camposExtra) {
      const valor = (extras[c.key] ?? "").trim();
      if (valor !== "") extra[c.key] = valor;
    }
    setErrores(SIN_ERRORES);
    setEnviando(true);
    try {
      if (inicial) {
        const cambios: Parameters<typeof modificarCliente>[1] = {};
        if (telefono.trim() !== (inicial.phone ?? "")) cambios.phone = telefono.trim();
        if (nombre.trim() !== (inicial.name ?? "")) cambios.name = nombre.trim();
        if (JSON.stringify(extra) !== JSON.stringify(soloTexto(inicial.extra_data))) cambios.extra_data = extra;
        if (Object.keys(cambios).length > 0) await modificarCliente(inicial.id, cambios);
      } else {
        await crearCliente({ phone: telefono.trim(), name: nombre.trim(), extra_data: extra });
      }
      alGuardar();
    } catch (err) {
      if (err instanceof ErrorApi && err.codigo === "cliente_duplicado") {
        const id = err.detalle["cliente_id"];
        setDuplicado(typeof id === "string" ? id : "");
        setErrores({ campos: { phone: "Ya existe un cliente con ese teléfono." }, general: null });
      } else if (err instanceof ErrorApi && err.codigo === "telefono_invalido") {
        setErrores({ campos: { phone: err.message }, general: null });
      } else if (err instanceof ErrorApi && err.codigo === "nombre_invalido") {
        setErrores({ campos: { name: err.message }, general: null });
      } else {
        setErrores(erroresDeApi(err));
      }
      setEnviando(false);
    }
  }

  return (
    <form
      className="formulario"
      aria-label={inicial ? "Editar cliente" : "Nuevo cliente"}
      onSubmit={(e) => void enviar(e)}
      noValidate
    >
      <Campo
        etiqueta="Teléfono"
        ayuda="Con o sin +54: por ejemplo 11 5555-1234 o +5491155551234."
        error={errores.campos["phone"]}
      >
        {(a) => <input {...a} type="tel" value={telefono} maxLength={64} onChange={(e) => setTelefono(e.target.value)} />}
      </Campo>
      {duplicado !== null && duplicado !== "" && (
        <p role="status">
          <Link to={`/clientes?cliente=${encodeURIComponent(duplicado)}`} onClick={alVerExistente}>
            Ver el cliente existente
          </Link>
        </p>
      )}
      <Campo etiqueta="Nombre" error={errores.campos["name"]}>
        {(a) => <input {...a} value={nombre} maxLength={200} onChange={(e) => setNombre(e.target.value)} />}
      </Campo>
      {camposExtra.map((c) => (
        <Campo key={c.key} etiqueta={c.label}>
          {(a) => (
            <input
              {...a}
              value={extras[c.key] ?? ""}
              maxLength={500}
              onChange={(e) => setExtras((x) => ({ ...x, [c.key]: e.target.value }))}
            />
          )}
        </Campo>
      ))}
      {errores.general && <p role="alert">{errores.general}</p>}
      <div className="acciones">
        <button type="submit" disabled={enviando}>
          Guardar
        </button>
        <button type="button" onClick={alCancelar}>
          Cancelar
        </button>
      </div>
    </form>
  );
}
