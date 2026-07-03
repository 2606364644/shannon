import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    globals: true,
    // dist-tsc/ 是 tsc -b 的 emit 产物 (tsconfig outDir), 不该被 vitest 拾取
    exclude: ["**/dist-tsc/**", "**/dist/**", "**/node_modules/**"],
  },
});
