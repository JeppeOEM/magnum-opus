import { defineConfig } from "vite";

const API_URL = process.env.VITE_API_URL ?? "http://localhost:3000";
const WS_URL = process.env.VITE_WS_URL ?? "ws://localhost:8083";

export default defineConfig({
  server: {
    port: 5173,
    proxy: {
      "/ws":      { target: WS_URL.replace(/^ws/, "http"), ws: true, changeOrigin: true },
      "^/heatmap$": { target: API_URL, changeOrigin: true },
      "/candles": { target: API_URL, changeOrigin: true },
    },
  },
  build: {
    rollupOptions: {
      input: {
        main: "index.html",
        heatmap: "heatmap.html",
      },
    },
  },
});
