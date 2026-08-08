import { chromium, type FullConfig } from '@playwright/test';
import { authenticator } from 'otplib';
import * as fs from 'fs';
import * as path from 'path';

/**
 * Authenticate one browser context per role and persist its storage state.
 *
 * Login is done through the real UI rather than by forging a session cookie, so the
 * two-step TOTP flow is itself exercised on every run — it is the gate in front of
 * the entire application and worth covering.
 *
 * It runs once here rather than per test for two reasons: each login is two form
 * submissions plus a redirect, and login/TOTP are rate limited to 5/min per IP
 * (accounts/views.py:60,92) with every request originating from 127.0.0.1. The
 * limiter is disabled in settings_e2e, but authenticating once per role keeps the
 * suite from depending on that.
 */

const AUTH_DIR = path.join(__dirname, '.auth');
const USERS_MANIFEST = '/opt/statementhub/.e2e/users.json';

interface RoleSpec {
  username: string;
  role: string;
  totp_secret: string;
  is_staff: boolean;
  assigned_entities: string[];
}

interface UsersManifest {
  password: string;
  roles: Record<string, RoleSpec>;
  sample_entities: { id: string; name: string; type: string }[];
}

async function globalSetup(config: FullConfig) {
  const baseURL =
    config.projects.find((p) => p.name === 'tier1')?.use?.baseURL ??
    'http://127.0.0.1:8100';

  if (!fs.existsSync(USERS_MANIFEST)) {
    throw new Error(
      `${USERS_MANIFEST} not found. It is written by manage.py e2e_bootstrap_users, ` +
        `which scripts/start_server.sh runs on boot. If the server started but this ` +
        `is missing, check the webServer output for a bootstrap failure.`,
    );
  }

  const manifest: UsersManifest = JSON.parse(fs.readFileSync(USERS_MANIFEST, 'utf-8'));
  fs.mkdirSync(AUTH_DIR, { recursive: true });

  const browser = await chromium.launch();

  try {
    for (const [key, spec] of Object.entries(manifest.roles)) {
      const context = await browser.newContext({ baseURL });
      const page = await context.newPage();

      await page.goto('/accounts/login/');
      await page.fill('input[name="username"]', spec.username);
      await page.fill('input[name="password"]', manifest.password);
      await page.click('button[type="submit"], input[type="submit"]');

      // Step two. The fixtures all have confirmed 2FA, so the TOTP page is expected
      // rather than optional — if login landed anywhere else, something is wrong and
      // should fail loudly here instead of producing a half-authenticated state that
      // fails confusingly in every test later.
      await page.waitForURL(/totp-verify/, { timeout: 30_000 }).catch(() => {
        throw new Error(
          `${spec.username}: expected redirect to /accounts/totp-verify/ after login but ` +
            `landed on ${page.url()}. Password wrong, or the fixture lost totp_confirmed.`,
        );
      });

      // otplib defaults match pyotp's defaults (SHA1, 6 digits, 30s), which is what
      // accounts/views.py verifies with.
      const code = authenticator.generate(spec.totp_secret);
      await page.fill('input[name="totp_code"]', code);
      await page.click('button[type="submit"], input[type="submit"]');

      await page.waitForURL((url) => !/totp-verify/.test(url.pathname), { timeout: 30_000 });

      // Require2FAMiddleware sends any user without confirmed 2FA to the setup page.
      // Landing there means the fixture is misconfigured, and every subsequent test
      // would silently assert against a setup form.
      if (/setup-2fa/.test(page.url())) {
        throw new Error(
          `${spec.username}: redirected to 2FA setup after verifying, which means ` +
            `totp_confirmed is not set on the fixture user.`,
        );
      }

      if (/login/.test(page.url())) {
        const error = await page.locator('.alert, .errorlist, .invalid-feedback').first()
          .textContent().catch(() => null);
        throw new Error(
          `${spec.username}: TOTP verification failed and returned to login. ` +
            `Page reported: ${error?.trim() ?? '(no message found)'}. ` +
            `A stale system clock on this host would also cause this.`,
        );
      }

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
