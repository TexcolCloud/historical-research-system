import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath, URL } from "node:url";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, "../..", "WORKBENCH_");
  const proxy = {
    "/api/v2": { target: env.WORKBENCH_API_ORIGIN || "http://127.0.0.1:18170" },
    "/uploads/": {
      target: env.WORKBENCH_UPLOAD_ORIGIN || "http://127.0.0.1:18171",
      changeOrigin: true,
      xfwd: true,
    },
  };
  return {
    build: {
      rolldownOptions: {
        input: {
          main: fileURLToPath(new URL("./index.html", import.meta.url)),
          platform: fileURLToPath(new URL("./platform.html", import.meta.url)),
        },
      },
    },
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
    },
    envDir: "../..",
    server: { fs: { strict: true, allow: ["."] }, proxy },
    preview: { proxy },
  };
});
