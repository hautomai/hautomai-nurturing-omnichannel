import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
    proxy: {
      // The API is always reached through the same origin in dev, so no CORS
      // juggling and the browser cookie/token story matches production.
      "/api": {
        target: process.env.VITE_API_PROXY ?? "http://localhost:8000",
        changeOrigin: true,
      },
      "/healthz": {
        target: process.env.VITE_API_PROXY ?? "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
    rollupOptions: {
      output: {
        // The canvas library is only needed on the builder route and is by far
        // the heaviest dependency, so keep it out of the entry chunk that the
        // dashboard loads first.
        manualChunks: {
          react: ["react", "react-dom", "react-router-dom"],
          flows: ["@xyflow/react"],
          query: ["@tanstack/react-query"],
        },
      },
    },
  },
});
