import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    // Первый тест файла грузит весь экран (модули, ELK, jsdom); при параллельном запуске на занятой машине
    // он не укладывается в 5 с по умолчанию.
    testTimeout: 20_000,
    hookTimeout: 20_000,
  },
});
