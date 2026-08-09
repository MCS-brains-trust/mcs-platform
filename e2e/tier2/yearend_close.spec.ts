import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import { startInstance, type Instance } from '../fixtures/instance';
import { loadUsers, loginAs } from '../fixtures/login';
import { dumpFigures, compareToBaseline, recordObserved } from '../fixtures/figures';

/**
 * Year-end close: trial balance import through to posted depreciation.
 *
 * The assertions below are the contracts these views state in their own docstrings —
 * a TB out of balance beyond $0.02 is refused, depreciation posting is idempotent,
 * opening balances are never touched — so a failure means the code stopped honouring
 * something it claims about itself.
 */

const PORT = 8201;
const IDS = JSON.parse(fs.readFileSync('/opt/statementhub/.e2e/fixture_entity.json', 'utf-8'));
const FY = IDS.current_fy;
const TB_DIR = '/opt/statementhub/.e2e/tb';

let instance: Instance;

test.describe.configure({ mode: 'serial' });

test.beforeAll(async () => {
  // instance.ts's BOOT_TIMEOUT_MS (180s) matches Tier 1's webServer.timeout for the
  // same branch-and-boot work. Playwright's global `timeout: 120_000` also bounds
  // this hook though, and a hook timeout firing first would produce a generic "Test
  // timeout exceeded" instead of a real boot failure -- so the hook budget must
  // safely exceed the boot budget (see tier2/instance.smoke.spec.ts).
  test.setTimeout(240_000);
  instance = await startInstance('yearend', PORT);
});

test.afterAll(async () => {
  await instance?.stop();
});

async function accountantPage(browser: any) {
  const users = loadUsers();
  const context = await browser.newContext({ baseURL: instance.baseURL });
  const page = await context.newPage();
  await loginAs(page, users.roles.senior, users.password);
  return page;
}

async function uploadTb(page: any, file: string) {
  await page.goto(`/years/${FY}/import/`);
  // TrialBalanceUploadForm (core/forms.py) renders a single `name="file"` input.
  // The generic `button[type="submit"]` selector also matches a hidden submit
  // button in the base template's account-menu dropdown (a logout form), so the
  // click has to be scoped to the form that actually holds the file input.
  await page.setInputFiles('input[type="file"]', `${TB_DIR}/${file}`);
  await page.click('form:has(input[type="file"]) button[type="submit"]');
  await page.waitForURL(/\/import\/review\/$/);
}

async function submitReview(page: any) {
  // review_tb_import.html's #commitBtn is disabled client-side by
  // import_wizard.js's checkBalance()/checkUnmapped() whenever the staged TB is out
  // of balance, has an unacknowledged rounding difference, or has a row without a
  // statement-line mapping -- a genuinely disabled <button> cannot be clicked, in
  // this browser or any other. That client gate is a UX convenience, not the
  // contract under test: commit_tb_import's own docstring/comments say the balance
  // check is revalidated server-side because the view does not trust the client.
  // Submitting the real #importForm directly (same action, same fields the template
  // renders) exercises exactly that server contract without depending on the
  // client-side convenience gate.
  await Promise.all([
    page.waitForLoadState('load'),
    page.evaluate(() => {
      (document.getElementById('importForm') as HTMLFormElement).submit();
    }),
  ]);
}

test('an out-of-balance trial balance is refused and writes nothing', async ({ browser }) => {
  const page = await accountantPage(browser);
  await uploadTb(page, 'tb_unbalanced.xlsx');

  // Staging is allowed; committing is where the balance gate lives.
  await submitReview(page);

  await expect(page.locator('body')).toContainText('out of balance');

  const figures = await dumpFigures(instance.dbName, FY, 'refused_unbalanced');
  expect(
    figures.trial_balance.filter((r: any) => r.source === 'tb_import'),
    'an out-of-balance import must not write trial balance lines',
  ).toHaveLength(0);

  await page.context().close();
});

test('a rounding difference needs the acknowledgement', async ({ browser }) => {
  const page = await accountantPage(browser);
  await uploadTb(page, 'tb_rounding.xlsx');

  await submitReview(page);
  await expect(page.locator('body')).toContainText('rounding');

  const figures = await dumpFigures(instance.dbName, FY, 'refused_rounding');
  expect(figures.trial_balance.filter((r: any) => r.source === 'tb_import')).toHaveLength(0);

  await page.context().close();
});

test('a balanced trial balance commits and balances', async ({ browser }) => {
  const page = await accountantPage(browser);
  await uploadTb(page, 'tb_balanced.xlsx');

  await submitReview(page);

  // The staged payload lives in the session. A per-process cache made a stale worker
  // copy shadow the database and this message appear at random (fixed in 41c8773),
  // so it is asserted against explicitly rather than inferred from the end state.
  await expect(page.locator('body')).not.toContainText('No staged TB import data found');

  const figures = await dumpFigures(instance.dbName, FY, 'after_tb_commit');
  expect(figures.totals.debit).toBe(figures.totals.credit);
  expect(figures.trial_balance.length).toBeGreaterThan(0);

  recordObserved('after_tb_commit', figures);
  expect(compareToBaseline('after_tb_commit', figures)).toEqual([]);

  await page.context().close();
});

test('posting without confirmed=1 does nothing', async ({ browser }) => {
  const page = await accountantPage(browser);

  const before = await dumpFigures(instance.dbName, FY, 'before_unconfirmed_post');

  // depreciation_post_to_tb's own guard -- `if request.POST.get("confirmed") != "1"`
  // -- fires before a single row is touched. The preview modal is the only real UI
  // path that ever sets that field, so a request missing it (a stale tab, a replayed
  // request, a client that skipped the modal) must be a strict no-op, not "posts
  // anyway because everything else about the request looked fine".
  await page.goto(`/years/${FY}/`);
  const status = await page.evaluate(async (fy) => {
    const token = (document.querySelector('[name=csrfmiddlewaretoken]') as HTMLInputElement)
      ?.value;
    const response = await fetch(`/years/${fy}/depreciation/post-to-tb/`, {
      method: 'POST',
      headers: { 'X-CSRFToken': token, 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams(), // deliberately no confirmed=1
      redirect: 'follow',
    });
    return response.status;
  }, FY);
  expect(status).toBeLessThan(400);

  const after = await dumpFigures(instance.dbName, FY, 'after_unconfirmed_post');
  expect(
    JSON.stringify(after),
    'a post without confirmed=1 must not change any figures',
  ).toBe(JSON.stringify(before));

  await page.context().close();
});

test('posting depreciation is idempotent and leaves opening balances alone', async ({
  browser,
}) => {
  const page = await accountantPage(browser);

  const before = await dumpFigures(instance.dbName, FY, 'before_depreciation_post');
  const openingBefore = before.trial_balance.map((r: any) => [
    r.account_code,
    r.opening_balance,
  ]);

  // confirmed=1 is set by the preview modal; the view refuses the post without it.
  await page.goto(`/years/${FY}/`);
  const post = async () =>
    page.evaluate(async (fy) => {
      const token = (document.querySelector('[name=csrfmiddlewaretoken]') as HTMLInputElement)
        ?.value;
      const body = new URLSearchParams({ confirmed: '1' });
      const response = await fetch(`/years/${fy}/depreciation/post-to-tb/`, {
        method: 'POST',
        headers: { 'X-CSRFToken': token, 'Content-Type': 'application/x-www-form-urlencoded' },
        body,
        redirect: 'follow',
      });
      return response.status;
    }, FY);

  expect(await post()).toBeLessThan(400);
  const first = await dumpFigures(instance.dbName, FY, 'after_depreciation_post');

  expect(await post()).toBeLessThan(400);
  const second = await dumpFigures(instance.dbName, FY, 'after_depreciation_post_twice');

  // The docstring promises "on repeat presses with an unchanged schedule the net
  // change is zero". Stacking is the classic failure here.
  expect(
    JSON.stringify(second),
    'pressing post-to-TB twice must not change the figures',
  ).toBe(JSON.stringify(first));

  const openingAfter = first.trial_balance.map((r: any) => [r.account_code, r.opening_balance]);
  expect(
    openingAfter.filter(([code]: any) =>
      openingBefore.some(([c]: any) => c === code),
    ),
    'posting depreciation must not touch opening balances',
  ).toEqual(openingBefore.filter(([code]: any) => openingAfter.some(([c]: any) => c === code)));

  expect(first.totals.debit).toBe(first.totals.credit);

  recordObserved('after_depreciation_post', first);
  expect(compareToBaseline('after_depreciation_post', first)).toEqual([]);

  await page.context().close();
});
