import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The built assets are served by the RetailOps FastAPI runtime from
// app/retailops/frontend/dist (see app_factory.py).
export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist" },
  server: {
    proxy: {
      "/v1": "http://localhost:8000",
      "/health": "http://localhost:8000",
    },
  },
});
