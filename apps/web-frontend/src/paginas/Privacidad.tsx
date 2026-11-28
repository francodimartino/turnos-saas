import { Marco } from "../compartido/Marco";
import { VERSION_POLITICA } from "../politica";

/** Texto provisorio de la política de privacidad, con su aviso visible, hasta que haya uno redactado. */
export function Privacidad() {
  return (
    <Marco>
      <h1>Política de privacidad</h1>
      <p role="note" className="aviso">
        <strong>TEXTO PROVISORIO: reemplazar por la política redactada</strong>
      </p>
      <p>
        Versión de la política: <span data-testid="version-politica">{VERSION_POLITICA}</span>
      </p>
      <h2>Qué datos usamos</h2>
      <p>
        Esta web todavía no recolecta datos. Cuando se puedan reservar turnos, el negocio va a necesitar tu nombre, tu
        teléfono y tu email, y el email se va a usar para confirmar que sos vos y para avisarte sobre tus turnos.
      </p>
      <h2>Para qué los usamos</h2>
      <p>Solo para gestionar tus turnos y avisarte sobre ellos. No vamos a vender tus datos.</p>
      <h2>Tus derechos</h2>
      <p>Podés pedir ver, corregir o borrar tus datos en cualquier momento.</p>
    </Marco>
  );
}
