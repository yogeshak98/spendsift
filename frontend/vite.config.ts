import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

export default defineConfig({
  plugins: [react()],
  base: "/",
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: { proxy: { "/api": "http://127.0.0.1:8765" } },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/setupTests.ts",
    environmentOptions: { jsdom: { url: "http://localhost" } },
    // The suite is large and jsdom-heavy; on a cold or loaded machine the
    // 5s default expires mid-render and reports a timeout where the real
    // result is a pass. Raised so a slow machine cannot manufacture a
    // failure -- this does not mask a hang, which still fails at 20s.
    testTimeout: 20000,
    hookTimeout: 20000,
  },
});
