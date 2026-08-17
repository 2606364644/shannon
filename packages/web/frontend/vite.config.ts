import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

export default defineConfig({
  plugins: [
    react({
      babel: {
        // React Compiler（spec §C）：编译期自动 memo 化。panicThreshold=none——编译失败
        // 的组件自动回退原实现，不挂构建。React 19 下无需 runtime 包。
        plugins: [["babel-plugin-react-compiler", { panicThreshold: "none" }]],
      },
    }),
  ],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    proxy: {
      "/api": {
        target: "http://localhost:7878",
        changeOrigin: true,
        ws: false,
      },
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          "react-vendor": ["react", "react-dom", "react-router", "react-router-dom", "scheduler"],
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    globals: true,
  },
});
