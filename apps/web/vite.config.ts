import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  test: {
    environment: "happy-dom",
    setupFiles: "./src/test/setup.ts",
    css: true,
  },
  plugins: [
    react(),
    VitePWA({
      strategies: "injectManifest",
      srcDir: "src",
      filename: "sw.ts",
      injectRegister: "auto",
      manifest: false,
      injectManifest: {
        // The shell is small and rarely changes shape; app data itself is
        // never precached here — that's what the Dexie-backed outbox/cache
        // in db/database.ts is for.
        globPatterns: ["**/*.{js,css,html,svg,webmanifest}"],
      },
      devOptions: {
        enabled: false,
      },
    }),
  ],
});
