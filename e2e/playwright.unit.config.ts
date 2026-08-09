import { defineConfig } from '@playwright/test';

/**
 * A second, deliberately separate Playwright config for pure-logic unit tests.
 *
 * figures.ts's comparator is plain TypeScript with no browser or server dependency,
 * but this repo has no standalone TS runner (no ts-node/tsx, and this Node version
 * predates built-in type stripping) — the only thing here that already knows how to
 * execute a .ts file is @playwright/test itself. Reusing it for a fixture-level test
 * means not pulling in a second test framework just to check one function, at the
 * cost of borrowing `test`/`expect` for something that never opens a browser.
 *
 * Kept out of playwright.config.ts's `projects` on purpose: that config's webServer
 * boots a full Django instance for every run, which would be a wildly disproportionate
 * cost for asserting on a JSON diff. Run with `npm run test:unit`.
 */
export default defineConfig({
  testDir: './fixtures',
  testMatch: '*.unit.spec.ts',
  outputDir: './test-results/unit',
  timeout: 10_000,
  reporter: [['list']],
});
