import { useEffect, useRef, useState } from "react";

/** Subconjunto de la API global que expone el script de Cloudflare Turnstile. */
interface ApiTurnstile {
  render(
    contenedor: HTMLElement,
    opciones: {
      sitekey: string;
      callback: (token: string) => void;
      "expired-callback": () => void;
      "error-callback": () => void;
    },
  ): string;
  remove(idWidget: string): void;
}

declare global {
  interface Window {
    turnstile?: ApiTurnstile;
  }
}

const TEXTO_FALLA_WIDGET = "No se pudo completar la verificación anti-bots. Recargá la página e intentá de nuevo.";
const TEXTO_FALLA_SCRIPT = "No se pudo cargar la verificación anti-bots. Revisá tu conexión y recargá la página.";

const URL_SCRIPT = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";

let cargaScript: Promise<ApiTurnstile> | null = null;

/** Carga el script una sola vez. Si ya hay `window.turnstile` (ya cargado) no agrega nada. */
function cargarScript(): Promise<ApiTurnstile> {
  if (window.turnstile) return Promise.resolve(window.turnstile);
  cargaScript ??= new Promise<ApiTurnstile>((resolver, rechazar) => {
    const script = document.createElement("script");
    script.src = URL_SCRIPT;
    script.async = true;
    script.onload = () => (window.turnstile ? resolver(window.turnstile) : rechazar(new Error("sin_turnstile")));
    script.onerror = () => {
      cargaScript = null;
      script.remove();
      rechazar(new Error("no_cargo"));
    };
    document.head.appendChild(script);
  });
  return cargaScript;
}

interface Props {
  siteKey: string;
  /** Token nuevo cuando el desafío se resuelve; `null` cuando vence o falla (hay que volver a resolverlo). */
  alCambiarToken: (token: string | null) => void;
}

/** Widget de Cloudflare Turnstile (la site key es pública). Se monta solo si hay site key: ver `Login`. */
export function WidgetTurnstile({ siteKey, alCambiarToken }: Props) {
  const contenedor = useRef<HTMLDivElement>(null);
  const aviso = useRef(alCambiarToken);
  const [falla, setFalla] = useState<string | null>(null);
  // Se actualiza en un efecto (no durante el render) y antes del efecto que monta el widget.
  useEffect(() => {
    aviso.current = alCambiarToken;
  }, [alCambiarToken]);

  useEffect(() => {
    let cancelado = false;
    let idWidget: string | undefined;
    let api: ApiTurnstile | undefined;
    cargarScript()
      .then((turnstile) => {
        if (cancelado || !contenedor.current) return;
        api = turnstile;
        idWidget = turnstile.render(contenedor.current, {
          sitekey: siteKey,
          callback: (token) => {
            setFalla(null);
            aviso.current(token);
          },
          "expired-callback": () => aviso.current(null),
          "error-callback": () => {
            aviso.current(null);
            setFalla(TEXTO_FALLA_WIDGET);
          },
        });
      })
      .catch(() => {
        if (cancelado) return;
        aviso.current(null);
        setFalla(TEXTO_FALLA_SCRIPT);
      });
    return () => {
      cancelado = true;
      if (api && idWidget !== undefined) api.remove(idWidget);
    };
  }, [siteKey]);

  return (
    <div>
      <div ref={contenedor} data-testid="turnstile" />
      {falla && <p role="alert">{falla}</p>}
    </div>
  );
}
