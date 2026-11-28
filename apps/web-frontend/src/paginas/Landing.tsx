import { Marco } from "../compartido/Marco";

const PASOS = [
  {
    titulo: "Armás tu negocio",
    texto: "Cargás tus servicios, tus profesionales y los horarios en que atendés.",
  },
  {
    titulo: "Compartís tu link",
    texto: "Cada negocio tiene su propia dirección, agendemos.ar/tu-negocio, para pasar por redes o mensajes.",
  },
  {
    titulo: "Tus clientes reservan",
    texto: "Van a poder elegir servicio y horario desde el celular, sin crearse una cuenta.",
  },
  {
    titulo: "Vos te enterás al instante",
    texto: "Te va a llegar un aviso por cada reserva, cancelación o cambio, y tu agenda va a estar siempre al día.",
  },
];

/** Landing comercial para negocios: propuesta, cómo funciona y contacto. */
export function Landing() {
  return (
    <Marco>
      <section aria-labelledby="propuesta">
        <h1 id="propuesta">Turnos online para tu negocio</h1>
        <p className="destacado">
          Tus clientes van a poder reservar, cancelar y mover su turno solos, a cualquier hora y desde el celular. Vos
          te dedicás a atender. Estamos preparando el servicio: todavía no está abierto.
        </p>
      </section>

      <section aria-labelledby="como-funciona">
        <h2 id="como-funciona">Cómo funciona</h2>
        <ol className="pasos">
          {PASOS.map((paso) => (
            <li key={paso.titulo}>
              <h3>{paso.titulo}</h3>
              <p>{paso.texto}</p>
            </li>
          ))}
        </ol>
      </section>

      <section aria-labelledby="contacto">
        <h2 id="contacto">Contacto</h2>
        <p>
          Si querés sumar tu negocio, escribinos por el mismo medio por el que llegaste a esta página y te contamos
          cómo empezar.
        </p>
      </section>
    </Marco>
  );
}
