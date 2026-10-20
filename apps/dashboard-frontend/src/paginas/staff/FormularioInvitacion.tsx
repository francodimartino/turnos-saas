import { useState, type FormEvent } from "react";
import type { Permiso } from "../../api/generado/permisos";
import { invitarStaff } from "../../api/staff";
import { ErrorApi } from "../../api/cliente";
import { Campo } from "../../compartido/Campo";
import { erroresDeApi, SIN_ERRORES, type ErroresFormulario } from "../../compartido/errores";
import { SelectorPermisos } from "./SelectorPermisos";

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

interface Props {
  alInvitar: (creado: boolean) => void;
  alCancelar: () => void;
}

export function FormularioInvitacion({ alInvitar, alCancelar }: Props) {
  const [nombre, setNombre] = useState("");
  const [email, setEmail] = useState("");
  const [permisos, setPermisos] = useState<Permiso[]>(["view_bookings"]);
  const [errores, setErrores] = useState<ErroresFormulario>(SIN_ERRORES);
  const [enviando, setEnviando] = useState(false);

  async function enviar(e: FormEvent) {
    e.preventDefault();
    const campos: Record<string, string> = {};
    if (nombre.trim() === "") campos["nombre"] = "Ingresá el nombre completo de la persona";
    if (!EMAIL.test(email.trim())) campos["email"] = "Ingresá un email válido";
    if (Object.keys(campos).length > 0) {
      setErrores({ campos, general: null });
      return;
    }
    setErrores(SIN_ERRORES);
    setEnviando(true);
    try {
      const r = await invitarStaff(email.trim(), nombre.trim(), permisos);
      alInvitar(r.creado);
    } catch (err) {
      const e2 = erroresDeApi(err);
      // El 422 `nombre_invalido` no nombra el campo: se muestra junto al campo del nombre.
      const nombreInvalido = err instanceof ErrorApi && err.codigo === "nombre_invalido";
      setErrores(nombreInvalido ? { campos: { nombre: e2.general ?? "Nombre inválido" }, general: null } : e2);
      setEnviando(false);
    }
  }

  return (
    <form className="formulario" aria-label="Invitar a una persona" onSubmit={(e) => void enviar(e)} noValidate>
      <Campo etiqueta="Nombre completo" error={errores.campos["nombre"]}>
        {(a) => <input {...a} value={nombre} maxLength={120} onChange={(e) => setNombre(e.target.value)} />}
      </Campo>
      <Campo etiqueta="Email de la persona" error={errores.campos["email"]}>
        {(a) => <input {...a} type="email" value={email} maxLength={254} onChange={(e) => setEmail(e.target.value)} />}
      </Campo>
      <SelectorPermisos legend="Permisos" seleccion={permisos} alCambiar={setPermisos} />
      {errores.general && <p role="alert">{errores.general}</p>}
      <div className="acciones">
        <button type="submit" disabled={enviando}>
          Enviar invitación
        </button>
        <button type="button" onClick={alCancelar}>
          Cancelar
        </button>
      </div>
    </form>
  );
}
