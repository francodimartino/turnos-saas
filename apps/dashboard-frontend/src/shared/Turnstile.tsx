import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { ErrorApi } from "../api/cliente";

/**
 * Widget de Cloudflare Turnstile, opcional: sin `VITE_TURNSTILE_SITE_KEY` no se carga ningún script ni se envía
 * ningún token (el backend tampoco lo exige si no tiene `TURNSTILE_SECRET_KEY`). La site key es pública por diseño.
 */

const URL_SCRIPT = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";

interface OpcionesRender {
  sitekey: string;
  callback: (token: string) => void;
  "expired-callback": () => void;
  "error-callback": () => void;
}

interface ApiTurnstile {
  render: (contenedor: HTMLElement, opciones: OpcionesRender) => string;
  reset: (id: string) => void;
  remove: (id: string) => void;
}

declare global {
  interface Window {
    turnstile?: ApiTurnstile;
  }
}

/** Se lee en cada uso (no al cargar el módulo) para que los tests puedan cambiarla. */
export function siteKeyTurnstile(): string {
  return (import.meta.env.VITE_TURNSTILE_SITE_KEY ?? "").trim();
}

let promesaScript: Promise<ApiTurnstile> | null = null;

function cargarScript(): Promise<ApiTurnstile> {
  if (window.turnstile) return Promise.resolve(window.turnstile);
  promesaScript ??= new Promise<ApiTurnstile>((resolver, rechazar) => {
    const s = document.createElement("script");
    s.src = URL_SCRIPT;
    s.async = true;
    s.onload = () => (window.turnstile ? resolver(window.turnstile) : rechazar(new Error("turnstile ausente")));
    s.onerror = () => {
      promesaScript = null;
      s.remove();
      rechazar(new Error("no se pudo cargar turnstile"));
    };
    document.head.appendChild(s);
  });
  return promesaScript;
}

const MENSAJES: Record<string, string> = {
  turnstile_invalido: "No pudimos verificar que sos una persona. Probá de nuevo.",
  turnstile_no_disponible:
    "La verificación anti-bots no está disponible en este momento. Probá de nuevo en unos segundos.",
};

const TEXTO_GENERICO_ESPERA = "Demasiados intentos. Esperá un rato antes de volver a probar.";

function textoEspera(segundos: number): string {
  if (segundos < 60) return `${segundos} ${segundos === 1 ? "segundo" : "segundos"}`;
  const minutos = Math.ceil(segundos / 60);
  return `${minutos} ${minutos === 1 ? "minuto" : "minutos"}`;
}

/** Mensaje en español para un error del backend; los códigos de Turnstile y de límite de intentos tienen texto propio. */
export function mensajeDeError(err: unknown, porDefecto: string): string {
  if (!(err instanceof ErrorApi)) return porDefecto;
  if (err.codigo === "demasiados_intentos") {
    return err.reintentarEnSeg === null
      ? TEXTO_GENERICO_ESPERA
      : `Demasiados intentos. Probá de nuevo en ${textoEspera(err.reintentarEnSeg)}.`;
  }
  return MENSAJES[err.codigo] ?? err.message;
}

interface EstadoTurnstile {
  /** Hay site key: hay que mostrar el widget y esperar el token. */
  activo: boolean;
  /** Token vigente, o null si todavía no se resolvió el desafío (o venció). */
  token: string | null;
  /** Pide un desafío nuevo (el token es de un solo uso: hay que reiniciar tras cada intento fallido). */
  reiniciar: () => void;
  /** Pieza visual a incluir en el formulario (null si no está activo). */
  widget: ReactNode;
}

export function useTurnstile(): EstadoTurnstile {
  const siteKey = siteKeyTurnstile();
  const activo = siteKey !== "";
  const contenedor = useRef<HTMLDivElement | null>(null);
  const idWidget = useRef<string | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [falla, setFalla] = useState<string | null>(null);

  useEffect(() => {
    if (!activo) return;
    let cancelado = false;
    cargarScript().then(
      (api) => {
        if (cancelado || !contenedor.current) return;
        idWidget.current = api.render(contenedor.current, {
          sitekey: siteKey,
          callback: (t) => {
            setToken(t);
            setFalla(null);
          },
          "expired-callback": () => setToken(null),
          "error-callback": () => {
            setToken(null);
            setFalla("No se pudo completar la verificación anti-bots. Recargá la página e intentá de nuevo.");
          },
        });
      },
      () => {
        if (!cancelado) setFalla("No se pudo cargar la verificación anti-bots. Revisá tu conexión y recargá la página.");
      },
    );
    return () => {
      cancelado = true;
      if (idWidget.current !== null) window.turnstile?.remove(idWidget.current);
      idWidget.current = null;
    };
  }, [activo, siteKey]);

  const reiniciar = useCallback(() => {
    setToken(null);
    if (idWidget.current !== null) window.turnstile?.reset(idWidget.current);
  }, []);

  return {
    activo,
    token,
    reiniciar,
    widget: activo ? (
      <div>
        <div ref={contenedor} data-testid="turnstile" />
        {falla && <p role="alert">{falla}</p>}
      </div>
    ) : null,
  };
}
