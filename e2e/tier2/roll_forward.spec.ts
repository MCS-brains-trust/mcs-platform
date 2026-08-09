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
 * A full replacement of all seven of the fixture's prior-year rows (commit_tb_import
 * deletes every non-adjustment line and recreates from the uploaded file, so omitting
 * a row would delete it) with one genuine double-entry correction on top: the client
 * found an unrecorded $1,000 Administration invoice, so Administration (debit) and
 * Trade Creditors (credit) both move by $1,000 -- Dr Expense / Cr Creditors, the
 * standard journal for a late invoice. Debits and credits both total 101,000.00; an
 * earlier draft of this amendment moved the $1,000 from Cash (debit) to Creditors
 * (credit) instead, which shifts total debits and total credits in *opposite*
 * directions and is not a valid double-entry (it left the file $2,000 out of balance
 * without either this comment or the assertion below noticing).
 */
async function writeAmendedPriorTbWorkbook(path: string): Promise<void> {
  const script = `
import openpyxl
wb = openpyxl.Workbook()
ws = wb.active
ws.title = "Trial Balance"
ws.append(("Account Code", "Account Name", "Debit", "Credit"))
for row in [
    ("1-1000", "Cash at Bank", 70000.00, 0.00),
    ("1-2000", "Plant and Equipment", 20000.00, 0.00),
    ("1-2100", "Accumulated Depreciation", 0.00, 4000.00),
    ("2-1000", "Trade Creditors", 0.00, 7000.00),
    ("3-1000", "Retained Earnings", 0.00, 60000.00),
    ("4-1000", "Sales", 0.00, 30000.00),
    ("6-1000", "Administration", 11000.00, 0.00),
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
  // 7 accounts: the fixture's five balance-sheet rows plus its Sales/Administration
  // P&L pair (core/e2e_fixture_data.py's PRIOR_YEAR_TB) -- 2026 starts with zero
  // TrialBalanceLine rows of its own, so every one of 2025's accounts is "new".
  await expect(page.locator('body')).toContainText('7 accounts to add');
  await Promise.all([
    page.waitForURL(new RegExp(`/years/${CURRENT_FY}/`)),
    page.click('#rerollForm button[type="submit"]'),
  ]);
  await expect(page.locator('body')).toContainText('Re-rolled forward to 2026');

  // Whether the seven rollover lines it created hold the *right* figures is checked
  // by the final test in this file, not here -- see this file's header comment on
  // why the check with a known-bad outcome has to be the last test in the file.
  const rolled = await dumpFigures(instance.dbName, CURRENT_FY, 'after_first_roll');
  expect(rolled.trial_balance.length).toBe(7);

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

test('P&L accounts carry the rolled-forward year as a comparative only, never as an opening balance', async ({}) => {
  // Sales (4-1000) and Administration (6-1000) are the fixture's only real P&L
  // accounts (core/e2e_fixture_data.py's PRIOR_YEAR_TB). Unlike the balance-sheet
  // accounts the final test below checks, P&L accounts are *supposed* to reset to
  // zero every year -- carrying the prior year's activity as a comparative, not as
  // an opening balance a fresh year's own trading would then stack on top of.
  const rolled = await dumpFigures(instance.dbName, CURRENT_FY, 'after_roll_forward_pl_check');
  const byCode = new Map<string, any>(rolled.trial_balance.map((r: any) => [r.account_code, r]));

  for (const code of ['4-1000', '6-1000']) {
    const row = byCode.get(code);
    expect(row, `${code} should exist as a rollover line in 2026`).toBeTruthy();
    for (const field of ['opening_balance', 'debit', 'credit', 'closing_balance']) {
      expect(
        row[field],
        `${code}.${field} must be zero -- a P&L account must never carry an opening balance`,
      ).toBe('0.00');
    }
  }
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
  // the year's net result: opening = prior closing + net P&L + income tax. Net P&L is
  // computed the same way the view itself defines it -- the sum of every non-BS
  // account's closing balance (debit-positive, so negative means a profit) -- from
  // 2025's own Sales/Administration pair (core/e2e_fixture_data.py's PRIOR_YEAR_TB),
  // not hand-typed, so this check exercises the real formula rather than the trivial
  // "net P&L happens to be zero" case an all-balance-sheet fixture would produce.
  // This fixture has no income-tax account, so tax is genuinely zero.
  const netPlResult = prior.trial_balance
    .filter((r: any) => !/^[123]/.test(r.account_code))
    .reduce((sum: number, r: any) => sum + parseFloat(r.closing_balance), 0);
  const taxAmount = 0;
  const retainedEarningsNow = rolledByCode.get('3-1000')?.opening_balance;
  const retainedEarningsExpected = (
    parseFloat(priorBsClosing.get('3-1000') as string) +
    netPlResult +
    taxAmount
  ).toFixed(2);
  if (retainedEarningsNow !== retainedEarningsExpected) {
    mismatches.push(
      `3-1000 (retained earnings): expected opening ${retainedEarningsExpected} ` +
        `(2025's closing ${priorBsClosing.get('3-1000')} + net P&L ${netPlResult.toFixed(2)} ` +
        `+ tax ${taxAmount.toFixed(2)}), got ${retainedEarningsNow}`,
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
    'E2E: found and posted an unrecorded $1,000 Administration invoice.',
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
  // A positive check, not just the absence of one specific error string: any of
  // commit_tb_import's four rejection paths (no staged data, a locked year, the
  // balance gate, the rounding gate) would leave this text absent too, and a test
  // that can only tell "not this one error" apart from "committed" cannot tell
  // "the app did nothing" from "the app did the wrong thing".
  await expect(page.locator('body')).toContainText(/Imported \d+ lines\./);

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
  // Only 2-1000 (Trade Creditors, 6,000→7,000) is expected: it is the one
  // *balance-sheet* account that moved. 6-1000 (Administration, 10,000→11,000) also
  // moved, but the endpoint's own documented job is to compare balance-sheet closing
  // positions only -- a P&L account correctly never appearing here is the promise
  // being kept, not a gap in this expectation.
  const expectedChangedCodes = ['2-1000'];
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
  // _is_balance_sheet_account() (core/views.py) is a pure, caller-agnostic
  // classifier -- it takes a `coa_sections` dict as a parameter and touches no
  // database itself, so it has nothing wrong with it in isolation. The bug is in its
  // FOUR independent callers, each of which builds that dict the same way and each
  // of which would need the same fix: `reroll_forward_diff`, `reroll_forward_apply`,
  // `reroll_forward` (which builds it twice -- once in its POST branch, once again
  // in its GET preview branch), and `_populate_rolled_forward_fy` (used by the
  // plain, brand-new-year `roll_forward` view; not exercised by this file's fixture,
  // since 2026 already exists, but carries the identical copy-pasted query). Every
  // one of the four does:
  //
  //   coa_sections = dict(
  //       ChartOfAccount.objects.filter(
  //           entity_type=entity.entity_type, is_active=True
  //       ).values_list("account_code", "section")
  //   )
  //
  // ChartOfAccount is the entity-TYPE template (386 rows for entity_type="company"
  // in this database) -- a *different*, unrelated model from this entity's own
  // EntityChartOfAccount, which none of the four callers ever consult. None of
  // _is_balance_sheet_account()'s three fallbacks (a numeric HandiLedger code range,
  // TrialBalanceLine.mapped_line_item, then this coa_sections dict) match this
  // fixture's entity: its account codes ("1-1000" etc.) are hyphenated and fail the
  // numeric parse; the fixture's EntityChartOfAccount rows are seeded with a real
  // `section` ("current_assets", "equity", ...) but never a `maps_to`, so
  // mapped_line_item is never set on any TrialBalanceLine created from them, whether
  // seeded directly or imported through the real upload UI (verified directly
  // against this exact fixture, both ways -- see task-9-report.md); and the 386-row
  // ChartOfAccount(entity_type="company") template has no entries at all for these
  // codes. So EVERY account -- including the balance-sheet ones -- falls through to
  // the numeric-code fallback, which also fails (a hyphenated code is not
  // `.isdigit()`), and _is_balance_sheet_account returns False across the board.
  //
  // Part A's consequence: every account is run through the roll's P&L branch,
  // which is correct for real P&L accounts (comparative only, opening zeroed,
  // resetting each year -- see the passing "P&L accounts carry ... as a comparative
  // only" test above, which this same misclassification happens to leave correct)
  // but silently erases a real balance-sheet position when misapplied to the
  // balance-sheet accounts -- the entire trial balance comes back as
  // prior_debit/prior_credit comparative figures only, with opening_balance and
  // closing_balance both zero, contradicting both the confirm page's own copy
  // ("Closing balances from 2025 will become opening balances") and the success
  // message shown after clicking Apply Changes. The retained-earnings check above
  // makes the same point with a genuine, non-zero net profit rather than the
  // trivial zero case: $20,000 of real Sales-less-Administration profit never
  // reaches 3-1000 at all.
  //
  // Part B's consequence: reroll_forward_diff filters both sides of its comparison
  // through the same broken coa_sections lookup, so it is just as blind --
  // confirmed directly (not just inferred) by altering 1-1000's closing_balance in
  // the database by hand against this exact fixture and re-requesting this exact
  // endpoint: it still returns change_count: 0. reroll_forward_apply carries the
  // identical fourth copy of the query and would fail the same way if this test
  // ever reached a state where it had something to apply -- it wasn't reachable
  // here because the diff it depends on never reports a change to apply. The
  // reconciliation tool this test exercises through the real UI cannot ever surface
  // a genuine prior-year correction for an entity using this chart-of-accounts
  // convention.
  expect(
    mismatches,
    'roll-forward and its reconciliation diff are both silently blind to this ' +
      "entity's balance sheet -- see the comment above this assertion for the " +
      'confirmed root cause',
  ).toEqual([]);
});
