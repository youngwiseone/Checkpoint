import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development run the backend with CHECKPOINT_DEV=1 and open the one-time link it prints;
// the auth cookie is host-scoped, so it also applies to the Vite dev server on 127.0.0.1.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: { "/api": { target: "http://127.0.0.1:8765", changeOrigin: false } },
  },
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 900 },
});
