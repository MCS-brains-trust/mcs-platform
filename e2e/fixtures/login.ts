import { type Page } from '@playwright/test';
import { authenticator } from 'otplib';
import * as fs from 'fs';

/**
 * The real two-step TOTP login, shared by both tiers.
 *
 * Tier 1 runs this once per role in global-setup and saves storage state. Tier 2
 * cannot reuse that state: cookies are not scoped by port, so a Tier 1 cookie would
 * be sent to a Tier 2 instance whose database has no such session, and the failure
 * would read as a permissions bug rather than a setup bug. Tier 2 calls this per
 * instance instead.
 */

const USERS_MANIFEST = '/opt/statementhub/.e2e/users.json';

export interface RoleSpec {
  username: string;
  role: string;
  totp_secret: string;
  is_staff: boolean;
  assigned_entities: string[];
}

export interface UsersManifest {
  password: string;
  roles: Record<string, RoleSpec>;
  sample_entities: { id: string; name: string; type: string }[];
}

export function loadUsers(): UsersManifest {
  if (!fs.existsSync(USERS_MANIFEST)) {
    throw new Error(
      `${USERS_MANIFEST} not found. It is written by manage.py e2e_bootstrap_users, ` +
        `which scripts/start_server.sh runs on boot. If the server started but this ` +
        `is missing, check the server output for a bootstrap failure.`,
    );
  }
  return JSON.parse(fs.readFileSync(USERS_MANIFEST, 'utf-8'));
}

export async function loginAs(page: Page, spec: RoleSpec, password: string): Promise<void> {
  await page.goto('/accounts/login/');
  await page.fill('input[name="username"]', spec.username);
  await page.fill('input[name="password"]', password);
  await page.click('button[type="submit"], input[type="submit"]');

  await page.waitForURL(/totp-verify/, { timeout: 30_000 }).catch(() => {
    throw new Error(
      `${spec.username}: expected redirect to /accounts/totp-verify/ after login but ` +
        `landed on ${page.url()}. Password wrong, or the fixture lost totp_confirmed.`,
    );
  });

  // otplib defaults match pyotp's (SHA1, 6 digits, 30s), which is what
  // accounts/views.py verifies with.
  const code = authenticator.generate(spec.totp_secret);
  await page.fill('input[name="totp_code"]', code);
  await page.click('button[type="submit"], input[type="submit"]');

  await page.waitForURL((url) => !/totp-verify/.test(url.pathname), { timeout: 30_000 });

  if (/setup-2fa/.test(page.url())) {
    throw new Error(
      `${spec.username}: redirected to 2FA setup after verifying, which means ` +
        `totp_confirmed is not set on the fixture user.`,
    );
  }

  if (/login/.test(page.url())) {
    const error = await page
      .locator('.alert, .errorlist, .invalid-feedback')
      .first()
      .textContent()
      .catch(() => null);
    throw new Error(
      `${spec.username}: TOTP verification failed and returned to login. ` +
        `Page reported: ${error?.trim() ?? '(no message found)'}. ` +
        `A stale system clock on this host would also cause this.`,
    );
  }
}
