import { useCallback, useState } from "react";
import { desconectarCalendar, estadoCalendar, iniciarConexionCalendar } from "../../api/calendar";
import type { Profesional } from "../../api/catalogo";
import { erroresDeApi } from "../../compartido/errores";
import { Estado } from "../../compartido/Estado";
import { formatearFechaHora } from "../../compartido/formato";
import { esUrlHttps, irAUrlExterna } from "../../compartido/navegacion";
import { useCarga } from "../../compartido/useCarga";

/**
 * Estado de la conexión de Google Calendar de un profesional y sus botones. El profesional vinculado a la sesión o
 * quien tiene `manage_staff` pueden usarlo: el backend decide y un 403 se muestra como mensaje.
 */
export function PanelCalendar({ profesional }: { profesional: Profesional }) {
  const cargar = useCallback(() => estadoCalendar(profesional.id), [profesional.id]);
  const { carga, recargar } = useCarga(cargar);
  const [trabajando, setTrabajando] = useState(false);
  const [confirmando, setConfirmando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function conectar() {
    setError(null);
    setTrabajando(true);
    try {
      const { url } = await iniciarConexionCalendar(profesional.id);
      if (!esUrlHttps(url)) {
        setError("El servidor devolvió una dirección de conexión inválida. Avisá a soporte.");
        setTrabajando(false);
        return;
      }
      irAUrlExterna(url);
    } catch (e) {
      setError(erroresDeApi(e).general ?? "No se pudo iniciar la conexión");
      setTrabajando(false);
    }
  }

  async function desconectar() {
    setError(null);
    setTrabajando(true);
    try {
      await desconectarCalendar(profesional.id);
      setConfirmando(false);
      recargar();
    } catch (e) {
      setError(erroresDeApi(e).general ?? "No se pudo desconectar");
    }
    setTrabajando(false);
  }

  return (
    <div className="panel" role="region" aria-label={`Google Calendar de ${profesional.name}`}>
      <h2>Google Calendar de {profesional.name}</h2>
      <Estado carga={carga} reintentar={recargar}>
        {(estado) => (
          <>
            {estado.conectado ? (
              <p role="status">
                Conectado
                {estado.conectado_desde ? ` desde el ${formatearFechaHora(estado.conectado_desde)}` : ""}. Los turnos se
                reflejan en su calendario.
              </p>
            ) : (
              <p role="status">No conectado. Los turnos no se reflejan en su calendario.</p>
            )}
            {!estado.google_configurado && !estado.conectado && (
              <p>
                La conexión con Google Calendar todavía no está disponible para este local. Avisá a soporte para que la
                active.
              </p>
            )}
            <div className="acciones">
              {!estado.conectado && (
                <button
                  type="button"
                  disabled={trabajando || !estado.google_configurado}
                  onClick={() => void conectar()}
                >
                  Conectar Google Calendar
                </button>
              )}
              {estado.conectado && !confirmando && (
                <button type="button" disabled={trabajando} onClick={() => setConfirmando(true)}>
                  Desconectar Google Calendar
                </button>
              )}
              {estado.conectado && confirmando && (
                <span className="confirmar">
                  ¿Desconectar el calendario de {profesional.name}? Los turnos nuevos dejan de reflejarse.{" "}
                  <button type="button" disabled={trabajando} onClick={() => void desconectar()}>
                    Confirmar desconexión
                  </button>{" "}
                  <button type="button" onClick={() => setConfirmando(false)}>
                    Cancelar
                  </button>
                </span>
              )}
            </div>
          </>
        )}
      </Estado>
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
