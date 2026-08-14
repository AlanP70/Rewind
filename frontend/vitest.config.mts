import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

/**
 * The frontend's first test runner. Phase 2 shipped without one, which left the
 * `describe()` assertions in its outstanding-gaps item unwritten; Phase 3's
 * dating UI is what justified adding it, so those land here too.
 *
 * No DOM environment and no testing-library. Everything worth pinning in these
 * two features is a pure function -- `describe` turns counts into a stage,
 * `describeDate` turns a document into one of four states -- and both were
 * written that way so the decisions could be tested without rendering. A jsdom
 * setup with no test needing it is a dependency installed for later.
 */
export default defineConfig({
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  test: {
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
  },
});
