import js from "@eslint/js";
import tseslint from "typescript-eslint";

/**
 * Shared flat config. Consumers spread it and add framework-specific configs.
 * Type-aware rules resolve the nearest tsconfig via the project service.
 */
export default tseslint.config(
  { ignores: ["**/dist/**", "**/.next/**", "**/generated/**", "**/coverage/**", "**/*.config.*"] },
  js.configs.recommended,
  ...tseslint.configs.strictTypeChecked,
  ...tseslint.configs.stylisticTypeChecked,
  {
    languageOptions: {
      parserOptions: {
        projectService: true,
      },
    },
    rules: {
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/consistent-type-imports": "error",
      "@typescript-eslint/restrict-template-expressions": ["error", { allowNumber: true }],
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
      "no-console": "error",
    },
  },
);
