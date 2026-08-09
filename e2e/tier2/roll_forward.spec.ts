import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import { execFile } from 'child_process';
import { promisify } from 'util';
import { startInstance, type Instance } from '../fixtures/instance';
import { loadUsers, loginAs } from '../fixtures/login';
import { dumpFigures } from '../fixtures/figures';

const execFileAsync = promisify(execFile);

/**
 * Roll-forward and comparatives.
 *
 * The central assertion — every balance-sheet account's opening balance in the new
 * year equals its closing balance in the prior year — is the invariant behind two
 * separate production fixes (f12a48d, de7d04d). It is absolute, so it needs no
 * baseline: if it does not hold, the figures are wrong regardless of what was blessed.
 * The final test below is exactly this check, and it is red: see its own comment for
 * what this suite found when it actually ran the flow.
 *
 * Two roll-forward implementations exist side by side in this app:
 *   - `roll_forward` (views.py) creates a brand-new next financial year. The fixture's
 *     2026 year already exists (seeded with prior_year=2025 but zero TrialBalanceLine
 *     rows), so this view's own year_label collision guard refuses to touch it — see
 *     the "duplicate year" test below, which is the one case this spec drives that view
 *     via its real UI link.
 *   - `reroll_forward` (the "legacy" full-page view at /years/<pk>/reroll-forward/)
 *     updates an *existing* next year in place by wiping and recreating its rollover
 *     lines. This is the one that actually has to run for THIS fixture, because 2026
 *     already exists as 2025's next_year. Its own template (reroll_forward_confirm.html)
 *     is real and renders a real <form>, but nothing else in the app links to it —
 *     confirmed by grepping every template for `core:reroll_forward` — so every use of
 *     this view below reaches it by direct navigation and drives its real form from there.
 *   - A second, JSON-API pair (`reroll_forward_diff` / `reroll_forward_apply`) backs the
 *     "Re-Roll Forward" button + modal that IS wired into financial_year_detail.html.
 *     That pair only ever *updates* TrialBalanceLine rows that already carry
 *     source='rollover' in the next year — it silently ignores any account that isn't
 *     already there (a documented limitation, not a bug: "New accounts ... require a
 *     full re-roll forward via the existing view"). It is driven for real wherever the
 *     fixture's state lets it do useful work.
 *
 * Ordering note: Playwright's serial mode skips every remaining test in a file after
 * the first failure. This suite found one confirmed defect with two visible
 * consequences (see the last test), so exactly one test is allowed to fail and it has
 * to be the last one in the file — matching yearend_close.spec.ts's own convention of
 * placing its single known-red test last.
 */

const PORT = 8202;
const IDS = JSON.parse(fs.readFileSync('/opt/statementhub/.e2e/fixture_entity.json', 'utf-8'));
const PRIOR_FY = IDS.prior_fy;
const CURRENT_FY = IDS.current_fy;
const AMENDED_TB_PATH = '/opt/statementhub/.e2e/tb/tb_prior_amended.xlsx';

let instance: Instance;

test.describe.configure({ mode: 'serial' });

test.beforeAll(async () => {
  // instance.ts's BOOT_TIMEOUT_MS (180s) matches Tier 1's webServer.timeout for the
  // same branch-and-boot work. Playwright's global `timeout: 120_000` also bounds
  // this hook though, and a hook timeout firing first would produce a generic "Test
  // timeout exceeded" instead of a real boot failure -- so the hook budget must
  // safely exceed the boot budget (see tier2/yearend_close.spec.ts).
  test.setTimeout(240_000);
  instance = await startInstance('rollfwd', PORT);
});

test.afterAll(async () => {
  await instance?.stop();
});

async function seniorPage(browser: any) {
  const users = loadUsers();
  const context = await browser.newContext({ baseURL: instance.baseURL });
  const page = await context.newPage();
  await loginAs(page, users.roles.senior, users.password);
  return page;
}

/**
 * Write the workbook used to amend the (already-finalised) prior year, via the same
 * openpyxl-based approach core/e2e_tb_workbooks.py uses for the sibling spec's fixtures
 * -- there is no xlsx-writing library in this package's devDependencies, and adding one
 * for a single test-only file is a bigger change than shelling out to the venv's Python,
 * which already has openpyxl installed for exactly this purpose.
 *
 * Shifts $1,000 from Cash at Bank to Trade Creditors (a plausible real correction: the
 * client found an unrecorded invoice) so the total stays 70,000/70,000 balanced and only
 * two of the five original rows actually move.
 */
async function writeAmendedPriorTbWorkbook(path: string): Promise<void> {
  const script = `
import openpyxl
wb = openpyxl.Workbook()
ws = wb.active
ws.title = "Trial Balance"
ws.append(("Account Code", "Account Name", "Debit", "Credit"))
for row in [
    ("1-1000", "Cash at Bank", 49000.00, 0.00),
    ("1-2000", "Plant and Equipment", 20000.00, 0.00),
    ("1-2100", "Accumulated Depreciation", 0.00, 4000.00),
    ("2-1000", "Trade Creditors", 0.00, 7000.00),
    ("3-1000", "Retained Earnings", 0.00, 60000.00),
]:
    ws.append(row)
wb.save(${JSON.stringify(path)})
`;
  await execFileAsync('/opt/statementhub/venv/bin/python', ['-c', script]);
}

/** Upload a TB workbook the same way a real accountant would: pick the file, submit
 * the upload form, land on the review page. Mirrors yearend_close.spec.ts's helper of
 * the same name -- duplicated rather than imported, because importing another spec
 * file would register its tests a second time under this file. */
async function uploadTb(page: any, fyId: string, filePath: string) {
  await page.goto(`/years/${fyId}/import/`);
  await page.setInputFiles('input[type="file"]', filePath);
  await page.click('form:has(input[type="file"]) button[type="submit"]');
  await page.waitForURL(/\/import\/review\/$/);
}

/** Commit the staged review the way a real accountant would end up doing once the
 * balance and mapping checks are satisfied -- see yearend_close.spec.ts's identical
 * helper for the full reasoning on why this calls the form's submit() directly rather
 * than clicking #commitBtn: the button's client-side disabled state and
 * import_wizard.js's own confirm()/alert() gate are a UX convenience already covered
 * by that spec's dedicated "disables the commit button in the browser" test, and
 * exercising them a second time here would just be redundant with no new coverage. */
async function submitReview(page: any) {
  await Promise.all([
    page.waitForLoadState('load'),
    page.evaluate(() => {
      (document.getElementById('importForm') as HTMLFormElement).submit();
    }),
  ]);
}

/** Click the live "Re-Roll Forward" button (financial_year_detail.html) and capture
 * the diff its onclick fetches -- the same "click and await the network effect"
 * pattern yearend_close.spec.ts's postDepreciationViaModal uses for its own modal. */
async function openRerollModalAndGetDiff(page: any, fyId: string): Promise<any> {
  await page.goto(`/years/${fyId}/`);
  const [response] = await Promise.all([
    page.waitForResponse(
      (r: any) => r.url().includes('reroll-forward-diff') && r.request().method() === 'GET',
    ),
    page.click('button[onclick="openRerollModal()"]'),
  ]);
  return response.json();
}

test('roll-forward is refused while the source year is unfinalised', async ({ browser }) => {
  const page = await seniorPage(browser);
  // The fixture's 2026 year is draft and has no next_year of its own, so
  // financial_year_detail.html renders the "Roll Forward" control as a disabled
  // <button onclick="alert(...)"> with no href at all -- clicking it never sends a
  // network request. The server-side guard this test exercises is therefore only
  // reachable by navigating to the URL directly; no UI click could ever produce it.
  await page.goto(`/years/${CURRENT_FY}/roll-forward/`);
  await expect(page.locator('body')).toContainText('must be finalised');
  await page.context().close();
});

test('rolling the finalised prior year into its existing next year succeeds', async ({
  browser,
}) => {
  const page = await seniorPage(browser);

  // The "Roll Forward" link on 2025's own detail page is not shown at all once 2026
  // already exists as its next_year -- the template switches to "Re-Roll Forward"
  // instead (see the JSDoc header). /years/<pk>/reroll-forward/ (the full-page,
  // wipe-and-recreate implementation) is the only view that can populate 2026's
  // TrialBalanceLine rows for the first time, and nothing in the rest of the UI
  // links to it, so it is reached directly and its real, rendered <form> is driven
  // from there.
  await page.goto(`/years/${PRIOR_FY}/reroll-forward/`);
  await expect(page.locator('body')).toContainText('5 accounts to add');
  await Promise.all([
    page.waitForURL(new RegExp(`/years/${CURRENT_FY}/`)),
    page.click('#rerollForm button[type="submit"]'),
  ]);
  await expect(page.locator('body')).toContainText('Re-rolled forward to 2026');

  // Whether the five rollover lines it created hold the *right* figures is checked
  // by the final test in this file, not here -- see this file's header comment on
  // why the check with a known-bad outcome has to be the last test in the file.
  const rolled = await dumpFigures(instance.dbName, CURRENT_FY, 'after_first_roll');
  expect(rolled.trial_balance.length).toBe(5);

  await page.context().close();
});

test('rolling forward a second time does not create a duplicate year', async ({ browser }) => {
  const page = await seniorPage(browser);
  const before = await dumpFigures(instance.dbName, CURRENT_FY, 'before_duplicate_attempt');

  // 2026 now exists as 2025's next_year (from the previous test), so
  // financial_year_detail.html no longer renders a "Roll Forward" link for 2025 at
  // all -- it shows "Re-Roll Forward" instead (see this file's header comment). The
  // plain create-a-new-year view is therefore reached directly; the real UI has no
  // control left that could send this exact request for 2025 in its current state.
  await page.goto(`/years/${PRIOR_FY}/roll-forward/`);
  await Promise.all([
    page.waitForURL(new RegExp(`/years/${PRIOR_FY}/`)),
    page.click('form button:has-text("Confirm Roll Forward")'),
  ]);
  await expect(page.locator('body')).toContainText('already exists');

  // No duplicate year means 2026's own figures are untouched by the attempt.
  const after = await dumpFigures(instance.dbName, CURRENT_FY, 'after_duplicate_attempt');
  expect(
    JSON.stringify(after),
    'a refused roll-forward must not mutate the year it would have collided with',
  ).toBe(JSON.stringify(before));

  await page.context().close();
});

test('the reroll diff reports no changes right after a fresh roll', async ({ browser }) => {
  const page = await seniorPage(browser);
  const diff = await openRerollModalAndGetDiff(page, PRIOR_FY);

  // Real key is "changes" (with a "change_count" sibling) -- confirmed against
  // reroll_forward_diff's actual JsonResponse, not the "differences"/"diff"/"rows"
  // guesses the draft plan made from reading the view alone.
  expect(
    diff.changes,
    'a year rolled forward moments ago should have nothing left to reconcile',
  ).toEqual([]);
  expect(diff.change_count).toBe(0);
  await expect(page.locator('#reroll-no-changes')).toBeVisible();

  await page.context().close();
});

test('every balance-sheet account should carry its prior-year closing balance forward, and the reroll diff should catch it if one later changes', async ({
  browser,
}) => {
  // Part B below reopens, re-imports and re-finalises 2025 -- several real round
  // trips, plus re-finalising transitions through in_review, which can trigger a
  // synchronous Tier 3 AI pass. This gets more room than the file's default budget.
  test.setTimeout(180_000);

  const mismatches: string[] = [];

  // ── Part A: the central invariant, against the roll two tests ago ────────────
  const prior = await dumpFigures(instance.dbName, PRIOR_FY, 'prior_before_amendment');
  const rolled = await dumpFigures(instance.dbName, CURRENT_FY, 'after_roll_forward');
  const rolledByCode = new Map<string, any>(
    rolled.trial_balance.map((r: any) => [r.account_code, r]),
  );

  // Balance-sheet accounts only (codes 1/2/3 in this fixture's own chart of accounts;
  // see core/e2e_fixture_data.py's CHART_OF_ACCOUNTS). Every one of them must show up
  // in the new year with opening_balance == its own prior-year closing_balance.
  const priorBsClosing = new Map<string, string>(
    prior.trial_balance
      .filter((r: any) => /^[123]/.test(r.account_code))
      .map((r: any) => [r.account_code, r.closing_balance]),
  );
  for (const [code, closing] of priorBsClosing) {
    const openingNow = rolledByCode.get(code)?.opening_balance;
    if (openingNow !== closing) {
      mismatches.push(`${code}: expected opening ${closing} (2025's closing), got ${openingNow}`);
    }
  }

  // The retained-earnings account (3-1000 in this fixture) additionally has to absorb
  // the year's net result: opening = prior closing + net P&L + income tax. 2025 has no
  // P&L accounts at all, so net P&L and tax are both zero here and the formula reduces
  // to "opening == prior closing" -- already covered by the loop above for 3-1000, but
  // named explicitly so a reader can see the retained-earnings promise was checked, not
  // just incidentally covered as one more balance-sheet row.
  const retainedEarningsNow = rolledByCode.get('3-1000')?.opening_balance;
  const retainedEarningsExpected = priorBsClosing.get('3-1000'); // + $0 net P&L + $0 tax
  if (retainedEarningsNow !== retainedEarningsExpected) {
    mismatches.push(
      `3-1000 (retained earnings): expected opening ${retainedEarningsExpected} ` +
        `(2025's closing + $0 net P&L + $0 tax), got ${retainedEarningsNow}`,
    );
  }

  // ── Part B: does the reroll diff catch a real subsequent change? ─────────────
  // Reopen 2025 (real modal: financial_year_detail.html's #reopenModal).
  const page = await seniorPage(browser);
  await page.goto(`/years/${PRIOR_FY}/`);
  await page.click('button[data-bs-target="#reopenModal"]');
  await page.locator('#reopenModal').waitFor({ state: 'visible' });
  await page.fill(
    '#reopenReason',
    'E2E: correcting a $1,000 unrecorded invoice found after finalisation.',
  );
  await Promise.all([
    page.waitForURL(new RegExp(`/years/${PRIOR_FY}/`)),
    page.click('#reopenSubmitBtn'),
  ]);
  await expect(page.locator('body')).toContainText('reopened');

  // Amend the trial balance (real upload + review + commit). 2025 is now 'reopened',
  // not 'finalised', so trial_balance_import's is_locked guard no longer blocks the
  // import -- see FinancialYear.is_locked in core/models.py.
  await writeAmendedPriorTbWorkbook(AMENDED_TB_PATH);
  await uploadTb(page, PRIOR_FY, AMENDED_TB_PATH);
  await submitReview(page);
  await expect(page.locator('body')).not.toContainText('No staged TB import data found');

  // Re-finalise (real button + its real confirm() dialog).
  await page.goto(`/years/${PRIOR_FY}/`);
  page.once('dialog', (dialog: any) => dialog.accept());
  await Promise.all([
    page.waitForURL(new RegExp(`/years/${PRIOR_FY}/`)),
    page.click('#finaliseBtn'),
  ]);
  await expect(page.locator('body')).toContainText('finalised');

  // Re-open the Re-Roll Forward modal and read the diff.
  const diff = await openRerollModalAndGetDiff(page, PRIOR_FY);
  const changedCodes = diff.changes.map((c: any) => c.account_code).sort();
  const expectedChangedCodes = ['1-1000', '2-1000']; // Cash 50,000→49,000; Creditors 6,000→7,000
  if (JSON.stringify(changedCodes) !== JSON.stringify(expectedChangedCodes)) {
    mismatches.push(
      `reroll diff: expected changed accounts ${JSON.stringify(expectedChangedCodes)} ` +
        `after the amendment, got ${JSON.stringify(changedCodes)}`,
    );
  }

  await page.context().close();

  // DELIBERATELY NOT BLESSED, and DELIBERATELY the last test in the file (see the
  // header comment). Both parts above fail, for one confirmed root cause:
  //
  // _is_balance_sheet_account() (core/views.py) classifies an account as
  // balance-sheet through three fallbacks in order -- a numeric HandiLedger code
  // range, TrialBalanceLine.mapped_line_item, then the entity-TYPE template
  // ChartOfAccount.section (a *different*, unrelated model from this entity's own
  // EntityChartOfAccount). None of the three match this fixture's entity: its
  // account codes ("1-1000" etc.) are hyphenated and fail the numeric parse; the
  // fixture's EntityChartOfAccount rows are seeded with a real `section`
  // ("current_assets", "equity", ...) but never a `maps_to`, so mapped_line_item is
  // never set on any TrialBalanceLine created from them, whether seeded directly or
  // imported through the real upload UI (verified directly against this exact
  // fixture, both ways -- see task-9-report.md); and the global
  // ChartOfAccount(entity_type="company") template used for the third fallback has
  // no entries at all for these codes. So EVERY account -- including the
  // balance-sheet ones -- falls through to the numeric-code fallback, which also
  // fails (a hyphenated code is not `.isdigit()`), and _is_balance_sheet_account
  // returns False across the board.
  //
  // Part A's consequence: every account is run through the roll's P&L branch,
  // which is correct for real P&L accounts (comparative only, opening zeroed,
  // resetting each year) but silently erases a real balance-sheet position when
  // misapplied here -- the entire $70,000/$70,000 trial balance comes back as
  // prior_debit/prior_credit comparative figures only, with opening_balance and
  // closing_balance both zero, contradicting both the confirm page's own copy
  // ("Closing balances from 2025 will become opening balances") and the success
  // message shown after clicking Apply Changes. This also means the "P&L accounts
  // carry as comparatives, not openings" promise is impossible to verify in
  // isolation on this fixture: with the classification collapsed, balance-sheet
  // accounts get the P&L treatment too, so there is no account left that would show
  // a *different* outcome if the promise were being kept.
  //
  // Part B's consequence: reroll_forward_diff filters both sides of its comparison
  // through the same _is_balance_sheet_account() call, so it is just as blind --
  // confirmed directly (not just inferred) by altering 1-1000's closing_balance in
  // the database by hand against this exact fixture and re-requesting this exact
  // endpoint: it still returns change_count: 0. The reconciliation tool this test
  // exercises through the real UI cannot ever surface a genuine prior-year
  // correction for an entity using this chart-of-accounts convention.
  expect(
    mismatches,
    'roll-forward and its reconciliation diff are both silently blind to this ' +
      "entity's balance sheet -- see the comment above this assertion for the " +
      'confirmed root cause',
  ).toEqual([]);
});
