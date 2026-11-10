import react from "@vitejs/plugin-react";
import { copyFileSync } from "node:fs";
import { resolve } from "node:path";
import { defineConfig, loadEnv } from "vite";

// VITE_BASE: the path the site is served from. "/" for the FastAPI or a root domain,
// "/FinSight/" for GitHub project Pages.
// VITE_API_BASE_URL: origin of the API when it is hosted elsewhere (empty = same origin).
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const base = env.VITE_BASE || "/";
  return {
    base,
    plugins: [
      react(),
      {
        // Static hosts such as GitHub Pages serve 404.html for unknown paths. A copy of
        // index.html there lets a refresh on /app/runs/<id> load the app, which then routes.
        name: "spa-404-fallback",
        closeBundle() {
          const dist = resolve(process.cwd(), "dist");
          try {
            copyFileSync(resolve(dist, "index.html"), resolve(dist, "404.html"));
          } catch {
            /* build output missing: nothing to copy */
          }
        },
      },
    ],
    server: {
      port: 5173,
      proxy: { "/api": { target: env.VITE_DEV_API || "http://127.0.0.1:8000", changeOrigin: false } },
    },
    preview: { port: 4173 },
    build: { sourcemap: false, target: "es2022" },
  };
});
