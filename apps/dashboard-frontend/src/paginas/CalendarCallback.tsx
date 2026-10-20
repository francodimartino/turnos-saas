import { useEffect, useRef } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { completarCallbackCalendar } from "../api/calendar";
import { ErrorApi } from "../api/cliente";
import { esResultadoConocido } from "./calendar/resultados";

/**
 * Vuelta de Google cuando `GOOGLE_OAUTH_REDIRECT_URI` apunta al dashboard: lee `code`, `state` y `error` de la
 * query, se los pasa al backend (que valida el `state` firmado) y vuelve a /profesionales con el resultado.
 * El `code` es de un solo uso: se canjea una única vez aunque el componente se monte dos veces.
 */
export function CalendarCallback() {
  const [consulta] = useSearchParams();
  const navegar = useNavigate();
  const enviado = useRef(false);

  useEffect(() => {
    if (enviado.current) return;
    enviado.current = true;
    const code = consulta.get("code") ?? "";
    const state = consulta.get("state") ?? "";
    const error = consulta.get("error") ?? "";

    function volver(resultado: string, profesional?: string) {
      const destino = new URLSearchParams({ google_calendar: resultado });
      if (profesional) destino.set("profesional", profesional);
      // `replace`: el `code` no queda en el historial del navegador.
      navegar(`/profesionales?${destino.toString()}`, { replace: true });
    }

    completarCallbackCalendar(code, state, error).then(
      (r) => volver("ok", r.profesional_id),
      (e: unknown) => volver(e instanceof ErrorApi && esResultadoConocido(e.codigo) ? e.codigo : "error"),
    );
  }, [consulta, navegar]);

  return <p role="status">Conectando con Google Calendar…</p>;
}
