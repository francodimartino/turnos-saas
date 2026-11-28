import { Route, Routes } from "react-router-dom";
import { Landing } from "./paginas/Landing";
import { NoEncontrada } from "./paginas/NoEncontrada";
import { PaginaNegocio } from "./paginas/PaginaNegocio";
import { Privacidad } from "./paginas/Privacidad";

export function App() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/privacidad" element={<Privacidad />} />
      <Route path="/:slug" element={<PaginaNegocio />} />
      <Route path="*" element={<NoEncontrada />} />
    </Routes>
  );
}
