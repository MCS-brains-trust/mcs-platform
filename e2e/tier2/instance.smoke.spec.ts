import { test, expect } from '@playwright/test';
import { startInstance, type Instance } from '../fixtures/instance';
import { loadUsers, loginAs } from '../fixtures/login';

/**
 * Proves the per-file instance rig works: its own database branch, its own port,
 * a real login against it, and a page that renders. Every other Tier 2 spec depends
 * on this machinery, so when they all fail at once this spec says whether the rig or
 * the application is at fault.
 */

const PORT = 8209;
let instance: Instance;

test.beforeAll(async () => {
  // instance.ts's BOOT_TIMEOUT_MS (180s) matches Tier 1's webServer.timeout for the
  // same branch-and-boot work, so it must not be lowered. Playwright's global
  // `timeout: 120_000` also bounds this hook, though, and a hook timeout firing
  // first would produce a generic "Test timeout exceeded" instead of this file's
  // specific diagnostics -- so the hook budget must safely exceed the boot budget.
  test.setTimeout(240_000);
  instance = await startInstance('smoke', PORT);
});

test.afterAll(async () => {
  await instance?.stop();
});

test('the instance serves its own branch and authenticates', async ({ browser }) => {
  expect(instance.dbName).toBe('sh_e2e_tier2_smoke');

  const context = await browser.newContext({ baseURL: instance.baseURL });
  const page = await context.newPage();
  const users = loadUsers();

  await loginAs(page, users.roles.admin, users.password);

  const response = await page.goto('/entities/');
  expect(response?.status()).toBe(200);

  await context.close();
});
