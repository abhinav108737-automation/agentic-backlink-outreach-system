import js from "@eslint/js";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["out/**", "dist/**", "node_modules/**"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    rules: {
      "@typescript-eslint/no-explicit-any": "error",
      "no-restricted-imports": ["error", { paths: [{ name: "electron", message: "Only main/preload may import electron." }] }],
    },
  },
  // main + preload are the only privileged zones
  { files: ["src/main/**", "src/preload/**", "tests/**"], rules: { "no-restricted-imports": "off" } },
);
