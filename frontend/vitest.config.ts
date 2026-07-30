import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

// ponytail: jsdom, the `@/` alias tsconfig already declares, and an explicit include glob.
// No coverage thresholds, no setup file (the one jest-dom import lives in the test that uses
// it), no custom matchers, and no @vitejs/plugin-react — Vitest 4's oxc transform compiles
// the JSX on its own.
//
// The include glob is deliberate rather than default: this is a pnpm workspace root, and the
// default pattern would wander into node_modules and the backend's pytest files.
// The alias is four lines rather than the vite-tsconfig-paths dependency; there is exactly
// one path mapping to keep in sync.
export default defineConfig({
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
  },
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
});
