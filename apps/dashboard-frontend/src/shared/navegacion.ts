/** Salida del SPA hacia otra URL (la pantalla de consentimiento de Google). Aislado para poder simularlo en tests. */
export function irAUrlExterna(url: string): void {
  window.location.assign(url);
}

/** Solo se sigue una URL https: nunca `javascript:` ni `data:`, aunque la mande el servidor. */
export function esUrlHttps(url: string): boolean {
  try {
    return new URL(url).protocol === "https:";
  } catch {
    return false;
  }
}
