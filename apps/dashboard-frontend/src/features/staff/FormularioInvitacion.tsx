import { useState, type FormEvent } from "react";
import { invitarStaff } from "../../api/staff";
import { Campo } from "../../shared/Campo";
import { erroresDeApi, SIN_ERRORES, type ErroresFormulario } from "../../shared/errores";
import { SelectorPermisos } from "./SelectorPermisos";

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

interface Props {
  alInvitar: (creado: boolean) => void;
  alCancelar: () => void;
}

export function FormularioInvitacion({ alInvitar, alCancelar }: Props) {
  const [email, setEmail] = useState("");
  const [permisos, setPermisos] = useState<string[]>(["view_bookings"]);
  const [errores, setErrores] = useState<ErroresFormulario>(SIN_ERRORES);
  const [enviando, setEnviando] = useState(false);

  async function enviar(e: FormEvent) {
    e.preventDefault();
    if (!EMAIL.test(email.trim())) {
      setErrores({ campos: { email: "Ingresá un email válido" }, general: null });
      return;
    }
    setErrores(SIN_ERRORES);
    setEnviando(true);
    try {
      const r = await invitarStaff(email.trim(), permisos);
      alInvitar(r.creado);
    } catch (err) {
      const e2 = erroresDeApi(err);
      // El backend nombra el campo como `email` en los 422 de forma y con `email_invalido` en el de formato.
      setErrores(e2);
      setEnviando(false);
    }
  }

  return (
    <form className="formulario" aria-label="Invitar a una persona" onSubmit={(e) => void enviar(e)} noValidate>
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
