import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backend = process.env.BACKEND_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    // The app always talks to its own origin; in dev Vite forwards to FastAPI.
    proxy: {
      "/ws": { target: backend.replace(/^http/, "ws"), ws: true },
      "/status": backend,
    },
  },
  test: {
    environment: "node",
  },
});
