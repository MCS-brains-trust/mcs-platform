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
