import { useState, type FormEvent } from "react";
import { guardarSettings, leerSettings, type SettingsLocal } from "../../api/catalogo";
import { Campo } from "../../compartido/Campo";
import { camposExtraDe, type CampoExtra } from "../../compartido/camposExtra";
import { ErrorApi } from "../../api/cliente";
import { erroresDeApi, SIN_ERRORES, type ErroresFormulario } from "../../compartido/errores";
import { Estado } from "../../compartido/Estado";
import { useCarga } from "../../compartido/useCarga";
import { usePermiso } from "../../compartido/usePermiso";

const CLAVE_HORAS = "booking_approval_timeout_hours";

function texto(valor: unknown): string {
  return typeof valor === "string" ? valor : "";
}

const mismos = (a: CampoExtra[], b: CampoExtra[]) => JSON.stringify(a) === JSON.stringify(b);

function FormularioLocal({ settings, puedeEditar, alGuardar }: { settings: SettingsLocal; puedeEditar: boolean; alGuardar: (s: SettingsLocal) => void }) {
  const [direccion, setDireccion] = useState(texto(settings["local_direccion"]));
  const [horarios, setHorarios] = useState(texto(settings["local_horarios"]));
  const [contacto, setContacto] = useState(texto(settings["local_contacto"]));
  const [horas, setHoras] = useState(settings[CLAVE_HORAS] === undefined ? "" : String(settings[CLAVE_HORAS]));
  const [extras, setExtras] = useState<CampoExtra[]>(camposExtraDe(settings["end_customer_extra_fields"]));
  const [errores, setErrores] = useState<ErroresFormulario>(SIN_ERRORES);
  const [enviando, setEnviando] = useState(false);
  const [guardado, setGuardado] = useState(false);

  async function enviar(e: FormEvent) {
    e.preventDefault();
    setGuardado(false);
    const campos: Record<string, string> = {};
    const horasLimpias = horas.trim();
    if (horasLimpias !== "" && !/^\d+$/.test(horasLimpias)) campos[CLAVE_HORAS] = "Ingresá un número entero de horas";
    else if (horasLimpias !== "" && (Number(horasLimpias) < 1 || Number(horasLimpias) > 168))
      campos[CLAVE_HORAS] = "Ingresá un número de horas entre 1 y 168";
    const extrasLimpios = extras.map((x) => ({ key: x.key.trim(), label: x.label.trim() }));
    if (extrasLimpios.some((x) => x.key === "")) campos["end_customer_extra_fields"] = "Cada dato adicional necesita un identificador";
    if (new Set(extrasLimpios.map((x) => x.key)).size !== extrasLimpios.length) campos["end_customer_extra_fields"] = "Los identificadores no pueden repetirse";
    if (Object.keys(campos).length > 0) {
      setErrores({ campos, general: null });
      return;
    }

    // Solo se envía lo que cambió: el backend mezcla claves de primer nivel y `null` borra la clave.
    const cambios: SettingsLocal = {};
    const textoCambio = (clave: string, nuevo: string) => {
      if (nuevo.trim() !== texto(settings[clave])) cambios[clave] = nuevo.trim() === "" ? null : nuevo.trim();
    };
    textoCambio("local_direccion", direccion);
    textoCambio("local_horarios", horarios);
    textoCambio("local_contacto", contacto);
    const horasActuales = settings[CLAVE_HORAS] === undefined ? "" : String(settings[CLAVE_HORAS]);
    if (horasLimpias !== horasActuales) cambios[CLAVE_HORAS] = horasLimpias === "" ? null : Number(horasLimpias);
    const extrasConLabel = extrasLimpios.map((x) => ({ key: x.key, label: x.label === "" ? x.key : x.label }));
    if (!mismos(extrasConLabel, camposExtraDe(settings["end_customer_extra_fields"]))) {
      cambios["end_customer_extra_fields"] = extrasConLabel.length === 0 ? null : extrasConLabel;
    }
    if (Object.keys(cambios).length === 0) {
      setErrores({ campos: {}, general: "No hay cambios para guardar" });
      return;
    }

    setErrores(SIN_ERRORES);
    setEnviando(true);
    try {
      const respuesta = await guardarSettings(cambios);
      setGuardado(true);
      alGuardar(respuesta.settings);
    } catch (err) {
      const errores = erroresDeApi(err, { porDefecto: CLAVE_HORAS });
      // Un 422 de forma (lista de errores de FastAPI) cuyo campo no está en esta pantalla se muestra como mensaje general.
      const sinCampoVisible = errores.general === null && errores.campos[CLAVE_HORAS] === undefined;
      setErrores(sinCampoVisible && err instanceof ErrorApi ? { campos: {}, general: err.message } : errores);
    } finally {
      setEnviando(false);
    }
  }

  function cambiarExtra(i: number, parte: Partial<CampoExtra>) {
    setExtras((actuales) => actuales.map((x, j) => (j === i ? { ...x, ...parte } : x)));
  }

  return (
    <form className="formulario" aria-label="Datos del local" onSubmit={(e) => void enviar(e)} noValidate>
      <fieldset disabled={!puedeEditar || enviando}>
        <Campo etiqueta="Dirección" error={errores.campos["local_direccion"]}>
          {(a) => <input {...a} value={direccion} maxLength={300} onChange={(e) => setDireccion(e.target.value)} />}
        </Campo>
        <Campo etiqueta="Horarios de atención" ayuda="Texto que el asistente le informa al cliente." error={errores.campos["local_horarios"]}>
          {(a) => <textarea {...a} value={horarios} maxLength={1000} onChange={(e) => setHorarios(e.target.value)} />}
        </Campo>
        <Campo etiqueta="Contacto" error={errores.campos["local_contacto"]}>
          {(a) => <input {...a} value={contacto} maxLength={300} onChange={(e) => setContacto(e.target.value)} />}
        </Campo>
        <Campo
          etiqueta="Horas para aprobar un turno"
          ayuda="Entre 1 y 168. Si nadie aprueba en ese plazo, la solicitud vence. Vacío: 24 horas."
          error={errores.campos[CLAVE_HORAS]}
        >
          {(a) => <input {...a} inputMode="numeric" value={horas} onChange={(e) => setHoras(e.target.value)} />}
        </Campo>

        <div role="group" aria-label="Datos adicionales de clientes">
          <h2>Datos adicionales de clientes</h2>
          <p>Campos extra que se piden o cargan al registrar a un cliente (por ejemplo, obra social).</p>
          {extras.map((x, i) => (
            <div className="fila" key={i}>
              <input aria-label={`Identificador del dato ${i + 1}`} value={x.key} onChange={(e) => cambiarExtra(i, { key: e.target.value })} />
              <input aria-label={`Nombre del dato ${i + 1}`} value={x.label} onChange={(e) => cambiarExtra(i, { label: e.target.value })} />
              <button type="button" onClick={() => setExtras((a) => a.filter((_, j) => j !== i))}>
                Quitar dato {i + 1}
              </button>
            </div>
          ))}
          {errores.campos["end_customer_extra_fields"] && <p role="alert">{errores.campos["end_customer_extra_fields"]}</p>}
          <button type="button" onClick={() => setExtras((a) => [...a, { key: "", label: "" }])}>
            Agregar dato adicional
          </button>
        </div>
      </fieldset>
      {errores.general && <p role="alert">{errores.general}</p>}
      {guardado && <p role="status">Cambios guardados</p>}
      {puedeEditar && (
        <button type="submit" disabled={enviando}>
          Guardar cambios
        </button>
      )}
    </form>
  );
}

export function PaginaLocal() {
  const puedeEditar = usePermiso("manage_schedule");
  const { carga, recargar } = useCarga(leerSettings);
  return (
    <section>
      <h1>Datos del local</h1>
      {!puedeEditar && <p>Solo podés ver los datos del local: modificarlos requiere un permiso que tu usuario no tiene.</p>}
      <Estado carga={carga} reintentar={recargar}>
        {({ settings }) => <FormularioLocal settings={settings} puedeEditar={puedeEditar} alGuardar={() => recargar()} />}
      </Estado>
    </section>
  );
}
