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
  //
  // Calling HTMLFormElement.submit() (rather than clicking the button) reaches the
  // server, but it bypasses TWO client-side layers, not one: the disabled button,
  // and also import_wizard.js's bindFormSubmit(), which attaches a 'submit' event
  // listener with its own alert()/confirm() balance and mapping checks -- submit()
  // does not fire the 'submit' event at all (unlike requestSubmit() or a real click),
  // so that listener never runs. Both layers are UX convenience on top of the same
  // server contract, but neither is exercised by any spec once this helper is used,
  // which is a real coverage gap -- see the dedicated
  // "...disables the commit button in the browser" test below, which covers the
  // disabled-button layer directly instead.
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

test('an out-of-balance trial balance disables the commit button in the browser', async ({
  browser,
}) => {
  // submitReview() above deliberately bypasses #commitBtn's disabled state (and
  // import_wizard.js's bindFormSubmit alert/confirm checks) to reach the server
  // gate directly -- see its comment. That leaves the client-side safeguard itself
  // completely untested, so this test asserts it directly: no form submission
  // involved, just that the real page actually disables the real button.
  const page = await accountantPage(browser);
  await uploadTb(page, 'tb_unbalanced.xlsx');

  await expect(page.locator('#commitBtn')).toBeDisabled();

  await page.context().close();
});

test('a rounding difference needs the acknowledgement', async ({ browser }) => {
  const page = await accountantPage(browser);
  await uploadTb(page, 'tb_rounding.xlsx');

  await submitReview(page);
  await expect(page.locator('body')).toContainText('rounding');

  const figures = await dumpFigures(instance.dbName, FY, 'refused_rounding');
  expect(
    figures.trial_balance.filter((r: any) => r.source === 'tb_import'),
    'a rounding difference without acknowledgement must not write trial balance lines',
  ).toHaveLength(0);

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

async function openDepreciationTab(page: any) {
  // active_tab is read straight off ?tab= (core/views.py), so this renders the
  // Depreciation pane as the visible one server-side -- no Bootstrap tab click
  // needed to reveal the "Post to Trial Balance" button underneath it.
  await page.goto(`/years/${FY}/?tab=depreciation`);
}

async function postDepreciationViaModal(page: any): Promise<number> {
  // Unlike review_tb_import.html's #commitBtn, depPostPreviewModal
  // (financial_year_detail.html) has no client-side disabling logic at all: it's a
  // plain Bootstrap modal wrapping a plain <form method="post"> with a real
  // <button type="submit">. So this one can be driven for real, and per review
  // should be -- opening the modal and clicking Confirm Post exercises the exact
  // same request a real accountant's click would send.
  await openDepreciationTab(page);
  await page.click('button:has-text("Post to Trial Balance")');
  await page.locator('#depPostPreviewModal').waitFor({ state: 'visible' });

  const [response] = await Promise.all([
    page.waitForResponse(
      (resp: any) =>
        resp.url().includes('/depreciation/post-to-tb/') && resp.request().method() === 'POST',
    ),
    page.click('#depPostPreviewModal button[type="submit"]'),
  ]);
  await page.waitForLoadState('load');
  return response.status();
}

test('posting without confirmed=1 does nothing', async ({ browser }) => {
  const page = await accountantPage(browser);

  const before = await dumpFigures(instance.dbName, FY, 'before_unconfirmed_post');

  // depreciation_post_to_tb's own guard -- `if request.POST.get("confirmed") != "1"`
  // -- fires before a single row is touched. Unlike the TB commit gate above, this
  // one has no UI path around it to worry about bypassing: the real "Confirm Post"
  // button always sends a hidden confirmed=1 field (see postDepreciationViaModal's
  // template), so there is no way to drive the browser into submitting this request.
  // This test is specifically exercising the server-side guard against a request
  // the UI itself cannot produce (a stale tab, a replayed request, a hand-crafted
  // POST), which is exactly the case where going around the UI is the right call
  // rather than a shortcut.
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

  // Both presses go through the real modal (see postDepreciationViaModal) so this
  // test proves the idempotency contract the way an accountant would actually
  // trigger it -- open the preview, click Confirm Post -- not via a hand-built
  // request that only resembles what the button sends.
  expect(await postDepreciationViaModal(page)).toBeLessThan(400);
  const first = await dumpFigures(instance.dbName, FY, 'after_depreciation_post');

  expect(await postDepreciationViaModal(page)).toBeLessThan(400);
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
