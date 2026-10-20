import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "node_modules", "src/api/generado"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: { globals: globals.browser },
    plugins: { "react-hooks": reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // El navegador nunca habla con Supabase: todo pasa por admin-backend (ARCHITECTURE.md §3.1.5).
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: ["@supabase/*", "supabase", "supabase-js"],
              message: "El frontend no usa el SDK de Supabase: hablá con admin-backend desde src/api.",
            },
          ],
        },
      ],
      // Los tokens viajan solo en cookies HttpOnly; nada de sesión en el almacenamiento del navegador.
      "no-restricted-globals": [
        "error",
        { name: "localStorage", message: "No se guarda la sesión en localStorage (ver README)." },
      ],
      "no-restricted-properties": [
        "error",
        { object: "window", property: "localStorage", message: "No se guarda la sesión en localStorage (ver README)." },
      ],
    },
  },
  {
    files: ["**/*.test.{ts,tsx}", "vite.config.ts", "scripts/**/*.mjs"],
    languageOptions: { globals: { ...globals.browser, ...globals.node } },
    rules: { "no-restricted-globals": "off", "no-restricted-properties": "off" },
  },
);
