import { useState, type FormEvent } from "react";
import { descartarLimpiezaCalendar } from "../api/panel";
import { mensajeDeError } from "../compartido/useCarga";

interface Props {
  tenantId: string;
  eventId: string;
  alDescartar: () => void;
}

/** Descartar una limpieza de Calendar pendiente: motivo obligatorio y confirmación explícita antes de enviar. */
export function DescartarLimpieza({ tenantId, eventId, alDescartar }: Props) {
  const [abierto, setAbierto] = useState(false);
  const [motivo, setMotivo] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [trabajando, setTrabajando] = useState(false);

  async function confirmar(e: FormEvent) {
    e.preventDefault();
    const limpio = motivo.trim();
    if (!limpio) {
      setError("El motivo es obligatorio");
      return;
    }
    setTrabajando(true);
    setError(null);
    try {
      await descartarLimpiezaCalendar(tenantId, eventId, limpio);
      alDescartar();
    } catch (err) {
      setError(mensajeDeError(err));
      setTrabajando(false);
    }
  }

  if (!abierto) {
    return (
      <button type="button" onClick={() => setAbierto(true)}>
        Descartar
      </button>
    );
  }
  return (
    <form onSubmit={confirmar} className="confirmacion" aria-label={`Descartar limpieza ${eventId}`}>
      <p>
        Vas a descartar la limpieza del evento {eventId}: el job dejará de reintentarla y el evento queda sin
        anonimizar en Google Calendar.
      </p>
      <label>
        Motivo
        <input value={motivo} onChange={(e) => setMotivo(e.target.value)} />
      </label>
      <button type="submit" disabled={trabajando}>
        Confirmar descarte
      </button>
      <button type="button" onClick={() => setAbierto(false)} disabled={trabajando}>
        Cancelar
      </button>
      {error && <p role="alert">{error}</p>}
    </form>
  );
}
