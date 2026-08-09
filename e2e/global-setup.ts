import { chromium, type FullConfig } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import { loadUsers, loginAs } from './fixtures/login';

/**
 * Authenticate one browser context per role and persist its storage state.
 *
 * Login goes through the real UI rather than a forged session cookie, so the
 * two-step TOTP flow — the gate in front of the entire application — is exercised
 * on every run. It runs once here rather than per test because each login is two
 * form submissions plus a redirect, and login/TOTP are rate limited to 5/min per IP
 * (accounts/views.py:60,92) with every request coming from 127.0.0.1.
 */

const AUTH_DIR = path.join(__dirname, '.auth');

async function globalSetup(config: FullConfig) {
  const baseURL =
    config.projects.find((p) => p.name === 'tier1')?.use?.baseURL ??
    'http://127.0.0.1:8100';

  const manifest = loadUsers();
  fs.mkdirSync(AUTH_DIR, { recursive: true });

  const browser = await chromium.launch();
  try {
    for (const [key, spec] of Object.entries(manifest.roles)) {
      const context = await browser.newContext({ baseURL });
      const page = await context.newPage();
      await loginAs(page, spec, manifest.password);
      await context.storageState({ path: path.join(AUTH_DIR, `${key}.json`) });
      console.log(`  authenticated ${spec.username} (${spec.role})`);
      await context.close();
    }
  } finally {
    await browser.close();
  }

  console.log(`storage state written for ${Object.keys(manifest.roles).length} roles`);
}

export default globalSetup;
