import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "node_modules"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: { globals: globals.browser },
    plugins: { "react-hooks": reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // El navegador nunca habla con Supabase (ARCHITECTURE.md §3.1.5).
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: ["@supabase/*", "supabase", "supabase-js"],
              message: "El frontend no usa el SDK de Supabase: todo pasa por un backend propio.",
            },
          ],
        },
      ],
      // Nada de datos de personas en el almacenamiento del navegador (ver README).
      "no-restricted-globals": [
        "error",
        { name: "localStorage", message: "No se guardan datos de personas en localStorage (ver README)." },
      ],
      "no-restricted-properties": [
        "error",
        { object: "window", property: "localStorage", message: "No se guardan datos de personas en localStorage (ver README)." },
      ],
    },
  },
  {
    files: ["**/*.test.{ts,tsx}", "vite.config.ts"],
    languageOptions: { globals: { ...globals.browser, ...globals.node } },
    rules: {
      "no-restricted-globals": "off",
      // vi.waitFor usa el timeout de Vitest (1 s) y no el asyncUtilTimeout de test/setup.ts: bajo carga vence sin que haya un bug.
      "no-restricted-properties": [
        "error",
        { object: "vi", property: "waitFor", message: "Usá waitFor de @testing-library/react: respeta asyncUtilTimeout de test/setup.ts." },
      ],
    },
  },
);
