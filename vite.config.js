import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// /workspace belongs to the browser router; existing API paths stay unchanged.
export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: Object.fromEntries(
      ["/auth", "/contracts", "/tasks", "/reviews", "/health"].map((path) => [
        path,
        "http://127.0.0.1:8000",
      ]),
    ),
  },
  build: { outDir: "dist", emptyOutDir: true },
});
