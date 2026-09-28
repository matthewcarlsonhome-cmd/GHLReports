// Vite build/dev-server config: the React plugin enables JSX + fast refresh,
// and the dev server is pinned to port 5173 (Vite's default, made explicit).
// The dev server listens on localhost only; keep it that way (no --host) on
// shared networks, because the pinned Vite version has dev-server advisories
// (docs/SECURITY-SPEC.md SEC-18). Production builds do not include it.
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
});
