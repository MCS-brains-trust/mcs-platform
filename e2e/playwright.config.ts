import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright configuration for the StatementHub regression suite.
 *
 * Two projects with deliberately different isolation models:
 *
 *   tier1  A read-only crawl of every GET route in the committed manifest. Because
 *          it never writes, all workers can share one Django instance and one
 *          database branch, which keeps a 213-route sweep fast.
 *
 *   tier2  Deep flows that import trial balances, post journals, generate documents
 *          and finalise years. Each spec file provisions its own database branch and
 *          its own Django instance (see fixtures/instance.ts), so a test that
 *          finalises a year — a one-way transition in this application — cannot
 *          affect any other file.
 */

const TIER1_PORT = 8100;
const TIER1_DB = 'sh_e2e_tier1';

export default defineConfig({
  testDir: '.',
  outputDir: './test-results',

  // The suite drives a real application against real third-party services
  // (Anthropic, Textract, Xero), so generous timeouts are correctness, not
  // slack: a live Eva call or an OCR round trip legitimately takes tens of seconds.
  timeout: 120_000,
  expect: { timeout: 15_000 },

  // Retries mask flakiness, and a regression suite that hides flakiness is worse
  // than one that fails. Kept at 0 locally; CI allows one retry only so a genuine
  // infrastructure blip does not block a merge, and any retry is surfaced in the
  // report for investigation.
  retries: process.env.CI ? 1 : 0,

  // Fail the run if a test is left focused or skipped-by-accident in CI.
  forbidOnly: !!process.env.CI,

  fullyParallel: false,
  workers: process.env.CI ? 2 : 4,

  reporter: [
    ['list'],
    ['html', { outputFolder: './playwright-report', open: 'never' }],
    ['json', { outputFile: './test-results/results.json' }],
  ],

  use: {
    baseURL: `http://127.0.0.1:${TIER1_PORT}`,
    // Artefacts on failure only. Traces are the single most useful thing to have
    // when a crawl of 213 routes reports one broken page.
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    actionTimeout: 20_000,
    navigationTimeout: 45_000,
    ignoreHTTPSErrors: false,
    ...devices['Desktop Chrome'],
  },

  globalSetup: './global-setup.ts',

  projects: [
    {
      name: 'tier1',
      testDir: './tier1',
      use: { baseURL: `http://127.0.0.1:${TIER1_PORT}` },
    },
    {
      name: 'tier2',
      testDir: './tier2',
      // No baseURL: each tier2 file gets its own instance on its own port and sets
      // its own base URL. A shared default here would be a footgun, because a test
      // that forgot to use its instance would silently hit the tier1 server and
      // write to the shared branch.
    },
  ],

  webServer: {
    command: `bash ./scripts/start_server.sh ${TIER1_DB} ${TIER1_PORT}`,
    url: `http://127.0.0.1:${TIER1_PORT}/accounts/login/`,
    // Branching the database, seeding fixtures and booting Django takes well over
    // Playwright's 60s default on a cold page cache.
    timeout: 180_000,
    // Never reuse: a leftover server from an interrupted run would be serving a
    // branch whose state is unknown, and the whole point of branching is a known
    // starting state.
    reuseExistingServer: false,
    stdout: 'pipe',
    stderr: 'pipe',
  },
});
