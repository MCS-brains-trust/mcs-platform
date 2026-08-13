/**
 * The roll-forward flow, parameterised by entity type.
 *
 * The central assertion — every balance-sheet account's opening balance in the new
 * year equals its closing balance in the prior year, except the retained-profits
 * account, which absorbs the year's result — is the invariant behind two separate
 * production fixes (f12a48d, de7d04d). It is absolute, so it needs no baseline: if it
 * does not hold, the figures are wrong regardless of what was blessed.
 *
 * This file exists because that invariant is not company-specific, but the accounts
 * it runs over are. Which account absorbs the year's result differs per entity type,
 * and so does the section that account lives in:
 *
 *   company      Retained earnings              equity
 *   trust        4199 Undistributed income      pl_appropriation
 *   partnership  4199 Unappropriated profits    capital_accounts
 *   sole_trader  4199 Undistributed income      capital_accounts
 *
 * Each entity type gets a thin spec file that calls describeRollForward() with its own
 * port, database branch and account codes. See
 * docs/superpowers/specs/2026-08-12-tier2-entity-type-fixtures-design.md.
 *
 * Two roll-forward implementations exist side by side in this app:
 *   - `roll_forward` (views.py) creates a brand-new next financial year. The fixture's
 *     next year already exists, so this path reports "already exists" and is exercised
 *     by the duplicate-attempt test below.
 *   - `reroll_forward` wipes and recreates the existing next year's lines. That is the
 *     only path that can populate the fixture's 2026 for the first time, and nothing
 *     in the UI links to it, so it is reached by URL and its real rendered form driven.
 *
 * Ordering note: Playwright's serial mode skips every remaining test in a file after
 * the first failure, so any test expected to be red has to be the last one in the
 * file — the convention yearend_close.spec.ts follows too. No entity type has an
 * expected failure: every test here should pass and tier2/known_failures.json is
 * empty. The ordering is kept anyway, because the next defect this file finds will
 * want it.
 */
import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import { execFile } from 'child_process';
import { promisify } from 'util';
import { startInstance, type Instance } from '../fixtures/instance';
import { loadUsers, loginAs } from '../fixtures/login';
import { dumpFigures, recordObserved, compareToBaseline } from '../fixtures/figures';
import { E2E_STATE_DIR, VENV_PYTHON } from '../fixtures/paths';

const execFileAsync = promisify(execFile);

export interface RollForwardOptions {
  /** Profile key: 'company' | 'trust' | 'partnership' | 'sole_trader'. */
  profile: string;
  /** Dedicated port for this file's Django instance. */
  port: number;
  /** Manifest filename under .e2e/, written by e2e_seed_fixture_entity. */
  manifest: string;
  /** Instance slug, which also names this file's database branch. */
  instanceSlug: string;
  /** The account the year's result must close into. */
  retainedProfitsCode: string;
  /**
   * Every account expected to carry a prior closing balance into the new year,
   * INCLUDING retainedProfitsCode (whose rule is the stricter one checked separately).
   *
   * This is an explicit list rather than a pattern because the account-code shapes
   * differ per chart. The original single-entity version of this test selected
   * balance-sheet accounts with /^[123]/, which works only for MYOB-style 1-/2-/3-
   * codes. Against the HandiLedger codes every real entity in this book uses it
   * misclassifies both ways: 1510 Accountancy is an expense but matches, and 4199 and
   * 4000.01 are capital accounts but do not.
   */
  balanceSheetCodes: string[];
  /** Accounts expected to carry a comparative only, never an opening balance. */
  plCodes: string[];
  /** Rows the reroll preview should offer to add — the prior year's own line count. */
  expectedAccountsToAdd: number;
  /** Expected row count in the rolled year's trial balance. */
  expectedRolledRows: number;
  /** Expected opening balance on retainedProfitsCode after the roll. */
  expectedRetainedOpening: string;
  /**
   * Checkpoint prefix. The company passes '' so its already-blessed checkpoint names
   * ('prior_before_amendment', 'after_roll_forward') are unchanged; others pass
   * 'trust:' and so on, which bless_figures.sh keys on directly.
   */
  checkpointPrefix: string;
  /**
   * The COMPLETE amended prior-year trial balance. commit_tb_import deletes every
   * non-adjustment line and recreates from the uploaded file, so omitting a row would
   * delete it.
   *
   * Follow the company's shape: one genuine double-entry correction, an unrecorded
   * $1,000 expense invoice, so the expense (debit) and trade creditors (credit) both
   * move by $1,000. An earlier draft moved $1,000 from cash to creditors instead,
   * which shifts total debits and credits in opposite directions and is not valid
   * double-entry — it left the file $2,000 out of balance unnoticed.
   */
  amendedPriorTb: Array<[string, string, number, number]>;
  /**
   * Filename for this profile's amended workbook. MUST be unique per profile: the
   * path is shared rig state under .e2e/tb/, and four spec files running concurrently
   * would otherwise each overwrite the others' workbook with different figures.
   */
  amendedTbFileName: string;
  /**
   * Accounts the reroll diff must report after the amendment, sorted. Both halves of
   * the correction land on the balance sheet: trade creditors directly, and the
   * retained-profits account because the expense reduces the year's profit. The
   * expense account itself correctly never appears — the endpoint compares
   * balance-sheet closing positions only, and a P&L account resets each year.
   */
  expectedChangedCodes: string[];
}

export function describeRollForward(opts: RollForwardOptions): void {
  const IDS = JSON.parse(fs.readFileSync(`${E2E_STATE_DIR}/${opts.manifest}`, 'utf-8'));
  const PRIOR_FY = IDS.prior_fy;
  const CURRENT_FY = IDS.current_fy;
  const AMENDED_TB_PATH = `${E2E_STATE_DIR}/tb/${opts.amendedTbFileName}`;
  const cp = (name: string) => `${opts.checkpointPrefix}${name}`;

  let instance: Instance;

  test.describe.configure({ mode: 'serial' });

  test.beforeAll(async () => {
    // instance.ts's BOOT_TIMEOUT_MS (180s) matches Tier 1's webServer.timeout for the
    // same branch-and-boot work. Playwright's global `timeout: 120_000` also bounds
    // this hook though, and a hook timeout firing first would produce a generic "Test
    // timeout exceeded" instead of a real boot failure -- so the hook budget must
    // safely exceed the boot budget (see tier2/yearend_close.spec.ts).
    test.setTimeout(240_000);
    instance = await startInstance(opts.instanceSlug, opts.port);
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
   * openpyxl-based approach core/e2e_tb_workbooks.py uses for the sibling spec's
   * fixtures -- there is no xlsx-writing library in this package's devDependencies,
   * and adding one for a single test-only file is a bigger change than shelling out to
   * the venv's Python, which already has openpyxl installed for exactly this purpose.
   */
  async function writeAmendedPriorTbWorkbook(path: string): Promise<void> {
    // Written the same atomic way core.e2e_support.atomic_write publishes the other
    // .e2e/tb workbooks: temp file in the same directory, then os.replace. This path is
    // shared rig state, so a concurrently booting instance reading the directory must
    // never catch a half-written xlsx -- and 0600 keeps it consistent with everything
    // else the rig writes there.
    const script = `
import os, tempfile, json
import openpyxl
wb = openpyxl.Workbook()
ws = wb.active
ws.title = "Trial Balance"
ws.append(("Account Code", "Account Name", "Debit", "Credit"))
for row in json.loads(${JSON.stringify(JSON.stringify(opts.amendedPriorTb))}):
    ws.append(tuple(row))
target = ${JSON.stringify(path)}
os.makedirs(os.path.dirname(target), exist_ok=True)
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(target), prefix=".tb_prior_amended.", suffix=".tmp")
os.close(fd)
try:
    wb.save(tmp)
    os.chmod(tmp, 0o600)
    os.replace(tmp, target)
except BaseException:
    if os.path.exists(tmp):
        os.unlink(tmp)
    raise
`;
    await execFileAsync(VENV_PYTHON, ['-c', script]);
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
   * than clicking #commitBtn. */
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

  test(`${opts.profile}: roll-forward is refused while the source year is unfinalised`, async ({ browser }) => {
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

  test(`${opts.profile}: rolling the finalised prior year into its existing next year succeeds`, async ({
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
    // Every one of the prior year's accounts is "new": 2026 starts with zero
    // TrialBalanceLine rows of its own.
    await expect(page.locator('body')).toContainText(
      `${opts.expectedAccountsToAdd} accounts to add`,
    );
    await Promise.all([
      page.waitForURL(new RegExp(`/years/${CURRENT_FY}/`)),
      page.click('#rerollForm button[type="submit"]'),
    ]);
    await expect(page.locator('body')).toContainText('Re-rolled forward to 2026');

    // The balance-sheet accounts carry an opening balance, the P&L accounts carry a
    // comparative only, and the $20,000 net profit between them (Sales 30,000 less the
    // expense 10,000 -- the same arithmetic in every profile) is absorbed into the
    // type-correct retained-profits account.
    //
    // For the company, two defects have moved this number, so it is worth being
    // precise about which:
    //   * It asserted 7 while the misclassification defect was live, but for the wrong
    //     reason -- every account read as P&L, nothing carried an opening balance, the
    //     net P&L summed to zero across the whole balanced trial balance, and no
    //     retained-profits line was created at all.
    //   * It then asserted 8, because retained profits was identified by the numeric
    //     code 4199 alone. That fixture's chart has no such account, so the result was
    //     closed into a synthesised 4199 line *beside* 3-1000, which kept its prior
    //     closing balance untouched.
    // (core/tests_rollforward_retained_profits.py pins the figures account by account.)
    const rolled = await dumpFigures(instance.dbName, CURRENT_FY, cp('after_first_roll'));
    expect(rolled.trial_balance.length).toBe(opts.expectedRolledRows);
    const retained = rolled.trial_balance.find(
      (l: any) => l.account_code === opts.retainedProfitsCode,
    );
    expect(retained?.opening_balance).toBe(opts.expectedRetainedOpening);

    await page.context().close();
  });

  test(`${opts.profile}: rolling forward a second time does not create a duplicate year`, async ({ browser }) => {
    const page = await seniorPage(browser);
    const before = await dumpFigures(
      instance.dbName,
      CURRENT_FY,
      cp('before_duplicate_attempt'),
    );

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
    const after = await dumpFigures(instance.dbName, CURRENT_FY, cp('after_duplicate_attempt'));
    expect(
      JSON.stringify(after),
      'a refused roll-forward must not mutate the year it would have collided with',
    ).toBe(JSON.stringify(before));

    await page.context().close();
  });

  test(`${opts.profile}: the reroll diff reports no changes right after a fresh roll`, async ({ browser }) => {
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

  test(`${opts.profile}: P&L accounts carry the rolled-forward year as a comparative only, never as an opening balance`, async ({}) => {
    // Unlike the balance-sheet accounts the final test below checks, P&L accounts are
    // *supposed* to reset to zero every year -- carrying the prior year's activity as
    // a comparative, not as an opening balance a fresh year's own trading would then
    // stack on top of.
    const rolled = await dumpFigures(
      instance.dbName,
      CURRENT_FY,
      cp('after_roll_forward_pl_check'),
    );
    const byCode = new Map<string, any>(
      rolled.trial_balance.map((r: any) => [r.account_code, r]),
    );

    for (const code of opts.plCodes) {
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

  test(`${opts.profile}: every balance-sheet account carries its prior-year closing balance forward, and the reroll diff catches it when one later changes`, async ({
    browser,
  }) => {
    // Part B below reopens, re-imports and re-finalises 2025 -- several real round
    // trips, plus re-finalising transitions through in_review, which can trigger a
    // synchronous Tier 3 AI pass. This gets more room than the file's default budget.
    test.setTimeout(180_000);

    const mismatches: string[] = [];

    // ── Part A: the central invariant, against the roll two tests ago ────────────
    const prior = await dumpFigures(instance.dbName, PRIOR_FY, cp('prior_before_amendment'));

    // Both checkpoints are recorded and compared here, before Part A's own
    // mismatches.push() calls below and before Part B's reopen/amend/re-finalise -- the
    // same "capture before an assertion can throw" placement as yearend_close.spec.ts's
    // Finding 3 fix.
    recordObserved(cp('prior_before_amendment'), prior);
    expect(compareToBaseline(cp('prior_before_amendment'), prior)).toEqual([]);

    const rolled = await dumpFigures(instance.dbName, CURRENT_FY, cp('after_roll_forward'));
    recordObserved(cp('after_roll_forward'), rolled);
    expect(compareToBaseline(cp('after_roll_forward'), rolled)).toEqual([]);
    const rolledByCode = new Map<string, any>(
      rolled.trial_balance.map((r: any) => [r.account_code, r]),
    );

    // Balance-sheet accounts, named explicitly by the caller -- see balanceSheetCodes
    // for why this is not a pattern match on the account code. Every one of them must
    // show up in the new year with opening_balance == its own prior-year closing.
    const bsCodes = new Set(opts.balanceSheetCodes);
    const priorBsClosing = new Map<string, string>(
      prior.trial_balance
        .filter((r: any) => bsCodes.has(r.account_code))
        .map((r: any) => [r.account_code, r.closing_balance]),
    );
    // The retained-profits account is the one balance-sheet account this rule does NOT
    // govern: it absorbs the year's net result, so its opening is deliberately not its
    // own closing balance. The check immediately below is its rule, and it is stricter.
    for (const [code, closing] of priorBsClosing) {
      if (code === opts.retainedProfitsCode) continue;
      const openingNow = rolledByCode.get(code)?.opening_balance;
      if (openingNow !== closing) {
        mismatches.push(
          `${code}: expected opening ${closing} (2025's closing), got ${openingNow}`,
        );
      }
    }

    // The retained-profits account additionally has to absorb the year's net result:
    // opening = prior closing + net P&L + income tax. Net P&L is computed the same way
    // the view itself defines it -- the sum of every non-balance-sheet account's
    // closing balance (debit-positive, so negative means a profit) -- from the
    // fixture's own Sales/expense pair, not hand-typed, so this exercises the real
    // formula rather than the trivial "net P&L happens to be zero" case an
    // all-balance-sheet fixture would produce.
    //
    // The prior closing defaults to 0 when the retained-profits account has no prior
    // line at all, which is the normal case for every profile except the company: a
    // 4199 that the prior year never used still has to receive the year's result.
    const netPlResult = prior.trial_balance
      .filter((r: any) => !bsCodes.has(r.account_code))
      .reduce((sum: number, r: any) => sum + parseFloat(r.closing_balance), 0);
    const taxAmount = 0;
    const retainedPriorClosing = parseFloat(
      priorBsClosing.get(opts.retainedProfitsCode) ?? '0',
    );
    const retainedNow = rolledByCode.get(opts.retainedProfitsCode)?.opening_balance;
    const retainedExpected = (retainedPriorClosing + netPlResult + taxAmount).toFixed(2);
    if (retainedNow !== retainedExpected) {
      mismatches.push(
        `${opts.retainedProfitsCode} (retained profits): expected opening ${retainedExpected} ` +
          `(2025's closing ${retainedPriorClosing.toFixed(2)} + net P&L ${netPlResult.toFixed(2)} ` +
          `+ tax ${taxAmount.toFixed(2)}), got ${retainedNow}`,
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
      'E2E: found and posted an unrecorded $1,000 expense invoice.',
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
    if (JSON.stringify(changedCodes) !== JSON.stringify(opts.expectedChangedCodes)) {
      mismatches.push(
        `reroll diff: expected changed accounts ${JSON.stringify(opts.expectedChangedCodes)} ` +
          `after the amendment, got ${JSON.stringify(changedCodes)}`,
      );
    }

    await page.context().close();

    // This test was red for a long time, through two different defects that each hid
    // the other. Both are now fixed in application code and it passes; the history is
    // kept because it is the reason for the shape of the checks above.
    //
    // 1. Classification. _is_balance_sheet_account() is a pure, caller-agnostic
    //    classifier, but each of its callers built its `coa_sections` argument from the
    //    entity-TYPE ChartOfAccount template and never consulted this entity's own
    //    EntityChartOfAccount -- a different model entirely. The company fixture's codes
    //    ("1-1000" etc.) are hyphenated, so they failed the numeric HandiLedger parse;
    //    its EntityChartOfAccount rows carry a real `section` but no `maps_to`, so
    //    mapped_line_item was never set; and the type template has no entries for these
    //    codes. Every account -- balance-sheet ones included -- therefore classified as
    //    P&L, so the whole balance sheet came back as comparatives only with
    //    opening_balance zeroed, and reroll_forward_diff was blind to a real prior-year
    //    correction for the same reason. Fixed by _coa_sections_for_entity(), which
    //    layers the entity's own chart over the template
    //    (core/tests_rollforward_classification.py).
    //
    // 2. Retained profits. With classification fixed, the accounts carried forward but
    //    the year's result did not land in the right place: retained profits was
    //    identified by the numeric code 4199 alone, which a hyphenated chart never
    //    carries, so the result was closed into a *synthesised* 4199 line beside
    //    3-1000. Total equity was right and the trial balance balanced, which is why it
    //    took a golden-figures comparison to see it -- but the entity ended up with two
    //    retained-earnings accounts and its real one untouched. reroll_forward_diff and
    //    _apply then compared each account's prior closing against its next-year
    //    opening with no notion of that absorption at all, so once the result did reach
    //    3-1000 they reported $20,000 of phantom drift on it, and applying that would
    //    have written the year's profit back out of equity and left 2026 out of
    //    balance. Fixed by _is_retained_profits_account() (name and mapped line item as
    //    well as code, the same three ways the income-tax line beside it was always
    //    found) and _expected_next_year_openings(), which both endpoints now reconcile
    //    against (core/tests_rollforward_retained_profits.py).
    expect(
      mismatches,
      `roll-forward and its reconciliation diff disagree with the ${opts.profile} ` +
        "fixture's balance sheet -- see the comment above this assertion for the two " +
        'defects that produced exactly this symptom before',
    ).toEqual([]);
  });
}
