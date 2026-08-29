import { defineConfig, type ProxyOptions } from "vite";
import react from "@vitejs/plugin-react";

const BACKEND = "http://127.0.0.1:8000";

// Several API prefixes are also client-side routes (/exceptions, /audit,
// /reconciliation, /copilot, /sources). A hard refresh on one of those is a
// browser *navigation*, which must render the SPA, while the app's own fetches
// must still reach the backend. Navigations ask for text/html; fetch() does
// not, so serve index.html for the former and proxy the latter.
function apiProxy(): ProxyOptions {
  return {
    target: BACKEND,
    changeOrigin: true,
    bypass: (req) =>
      (req.headers.accept ?? "").includes("text/html") ? "/index.html" : undefined,
  };
}

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/auth": apiProxy(),
      "/health": apiProxy(),
      "/reconciliation": apiProxy(),
      "/exceptions": apiProxy(),
      "/audit": apiProxy(),
      "/copilot": apiProxy(),
      "/ingestion": apiProxy(),
      "/integrations": apiProxy(),
    },
  },
});
