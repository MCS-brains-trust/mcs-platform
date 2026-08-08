import { test as base, type Page } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

/**
 * Role-scoped page fixtures.
 *
 * Each role gets a pre-authenticated page built from the storage state saved by
 * global-setup. Permissions were one of the defect classes fixed in 24e5e09, so
 * "which role can reach this page" is asserted rather than assumed, which needs more
 * than one authenticated identity available to tests.
 */

const AUTH_DIR = path.join(__dirname, '..', '.auth');
const USERS_MANIFEST = '/opt/statementhub/.e2e/users.json';

export type RoleKey = 'admin' | 'senior' | 'accountant' | 'office_admin' | 'read_only';

export const ROLE_KEYS: RoleKey[] = [
  'admin',
  'senior',
  'accountant',
  'office_admin',
  'read_only',
];

/** Roles that can_view_all_entities (accounts/models.py:62). */
export const ROLES_SEEING_ALL_ENTITIES: RoleKey[] = ['admin', 'senior', 'office_admin'];

export interface RouteRecord {
  name: string | null;
  path: string;
  namespace: string | null;
  status: 'crawlable' | 'excluded' | 'unresolved';
  category: string | null;
  reason: string | null;
  url: string | null;
  params: Record<string, string>;
}

export interface RouteManifest {
  summary: Record<string, unknown>;
  routes: RouteRecord[];
}

export function loadRouteManifest(): RouteManifest {
  const p = path.join(__dirname, '..', 'tier1', 'routes.manifest.json');
  if (!fs.existsSync(p)) {
    throw new Error(
      `route manifest missing at ${p}. Regenerate with:\n` +
        `  python3 manage.py e2e_dump_routes --settings=config.settings_e2e`,
    );
  }
  return JSON.parse(fs.readFileSync(p, 'utf-8'));
}

export function loadUsersManifest() {
  return JSON.parse(fs.readFileSync(USERS_MANIFEST, 'utf-8'));
}

/** Build an authenticated page for a role, from the saved storage state. */
export async function pageForRole(browser: any, role: RoleKey, baseURL?: string): Promise<Page> {
  const statePath = path.join(AUTH_DIR, `${role}.json`);
  if (!fs.existsSync(statePath)) {
    throw new Error(`no storage state for role '${role}' — global setup did not complete`);
  }
  const context = await browser.newContext({ storageState: statePath, baseURL });
  return context.newPage();
}

type RoleFixtures = {
  [K in RoleKey as `${K}Page`]: Page;
};

export const test = base.extend<RoleFixtures>({
  adminPage: async ({ browser, baseURL }, use) => {
    const page = await pageForRole(browser, 'admin', baseURL);
    await use(page);
    await page.context().close();
  },
  seniorPage: async ({ browser, baseURL }, use) => {
    const page = await pageForRole(browser, 'senior', baseURL);
    await use(page);
    await page.context().close();
  },
  accountantPage: async ({ browser, baseURL }, use) => {
    const page = await pageForRole(browser, 'accountant', baseURL);
    await use(page);
    await page.context().close();
  },
  office_adminPage: async ({ browser, baseURL }, use) => {
    const page = await pageForRole(browser, 'office_admin', baseURL);
    await use(page);
    await page.context().close();
  },
  read_onlyPage: async ({ browser, baseURL }, use) => {
    const page = await pageForRole(browser, 'read_only', baseURL);
    await use(page);
    await page.context().close();
  },
});

export { expect } from '@playwright/test';
