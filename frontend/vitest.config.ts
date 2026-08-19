import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    // Mirror the tsconfig path alias so tests import the same modules the app does.
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
});
