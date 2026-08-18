// Vite build/dev-server config: the React plugin enables JSX + fast refresh,
// and the dev server is pinned to port 5173 (Vite's default, made explicit).
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
});
