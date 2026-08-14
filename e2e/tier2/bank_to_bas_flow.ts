/**
 * The bank-statement-to-BAS flow, parameterised by entity type.
 *
 * Every BAS label asserted here is hand-computed from the fixture's transaction
 * table, not baselined -- these figures go to the ATO, and a baseline blesses
 * whatever the code produced the first time it ran. See
 * docs/superpowers/specs/2026-08-14-bank-to-bas-tier2-design.md.
 */
import { test, expect, type Response } from '@playwright/test';
import * as fs from 'fs';
import { startInstance, type Instance } from '../fixtures/instance';
import { loadUsers, loginAs } from '../fixtures/login';
import { E2E_STATE_DIR, REPO_DIR } from '../fixtures/paths';

export interface BankToBasOptions {
  profile: string;
  port: number;
  manifest: string;
  instanceSlug: string;
  checkpointPrefix: string;
}

export function describeBankToBas(opts: BankToBasOptions): void {
  const IDS = JSON.parse(fs.readFileSync(`${E2E_STATE_DIR}/${opts.manifest}`, 'utf-8'));
  const FY = IDS.current_fy;

  let instance: Instance;
  // The review job detail page (/review/<uuid>/), captured by the upload test and
  // read by every test after it -- not confirm-import's own redirect target, which
  // lands on the FY detail page instead (see the upload test). Serial mode
  // guarantees the ordering; a parallel file would need this per-test.
  let reviewJobUrl = '';

  test.describe.configure({ mode: 'serial' });

  test.beforeAll(async () => {
    // The hook budget must exceed instance.ts's 180s boot budget, or a hook
    // timeout fires first and reports a generic timeout instead of the real
    // boot failure.
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

  test(`${opts.profile}: the fixture entity has a GST dashboard`, async ({ browser }) => {
    const page = await seniorPage(browser);
    await page.goto(`${instance.baseURL}/years/${FY}/gst/`);
    await expect(page.locator('body')).toContainText('Activity Statement');

    // A bare "Activity Statement" match passes whether or not the entity is
    // GST-registered: gst_activity_statement.html:17 renders "GST Activity
    // Statement" in its <h4> unconditionally. The one property the bank_bas
    // fixture (Task 3) exists to establish -- is_gst_registered: True -- is only
    // proven by checking that the not-registered warning
    // (gst_activity_statement.html:62-67, `{% if not is_gst_registered %}`) is
    // absent. If the fixture entity were ever seeded with is_gst_registered:
    // False, this assertion fails: the template would render that block and
    // this text would be present.
    await expect(page.locator('body')).not.toContainText(
      'This entity is not marked as GST registered',
    );

    await page.context().close();
  });

  const STATEMENT_PDF = `${REPO_DIR}/e2e/fixtures/statements/cba_sample.pdf`;

  test(`${opts.profile}: the statement parses and imports all six transactions`, async ({ browser }) => {
    const page = await seniorPage(browser);
    await page.goto(`${instance.baseURL}/years/${FY}/?tab=review`);

    await page.locator('button[data-bs-target="#uploadBankStatementModal"]').click();
    await page.locator('#uploadBankStatementModal').waitFor({ state: 'visible' });

    // Map the bank account before anything else in the modal. This is the genuine
    // first-time-user path, not a workaround: `active_bank_mapping` (core/views.py)
    // is only ever populated from a BankAccountMapping tied to THIS entity's own
    // imported statements, so a freshly seeded fixture always starts unmapped no
    // matter what Task 3 seeds directly -- there is no fixture shortcut here
    // (Ruling 17). isBankMapped() gates #fyUploadSubmitBtn on exactly this, and the
    // mapped tb_account_code also becomes the contra account Task 6's double-entry
    // posting depends on. IDS.bank_account_code ("2000", Cash at bank) is the code
    // Task 3's fixture chart actually carries.
    await page.fill('#wizardBankSearch', IDS.bank_account_code);
    await page.locator(`.wizard-bank-pick[data-code="${IDS.bank_account_code}"]`).click();
    // #wizardBankMapped only proves *a* mapping saved; #wizardBankSelected
    // (financial_year_detail.html:3081) records which account was actually picked,
    // and Task 6's contra entry depends on it being this one.
    await expect(page.locator('#wizardBankSelected')).toHaveValue(
      `${IDS.bank_account_code} - Cash at bank`,
    );
    await page.locator('#wizardBankSaveBtn').click();
    await expect(page.locator('#wizardBankMapped')).toHaveValue('1');

    // #fyFileInput carries .d-none; setInputFiles works on hidden inputs.
    await page.setInputFiles('#fyFileInput', STATEMENT_PDF);
    await page.locator('#fyUploadSubmitBtn').click();

    // The page JS posts to /parse-statement/ per file, then redirects.
    await page.waitForURL(/\/upload-preview\//, { timeout: 60_000 });

    // Six rows offered for import. A zero here is the exact defect b073cca
    // fixed: bank detected, header matched, no transactions extracted.
    await expect(page.locator('#importCount')).toHaveText('6');

    // The click handler's first act is a native confirm() dialog
    // (upload_preview.html) before it ever fetches -- accept it like the real
    // reviewer would, the same pattern roll_forward_flow.ts:466 uses for
    // #finaliseBtn. Its message folds in a free reconciliation check across all
    // six transactions (opening + Sum(amounts) vs closing, upload_preview.html:545-559)
    // that our two explicit sign assertions below don't cover on their own, so
    // capture it and assert on it rather than blind-accepting.
    let confirmDialogMessage = '';
    page.once('dialog', (dialog: any) => {
      confirmDialogMessage = dialog.message();
      dialog.accept();
    });
    const [confirmResponse] = await Promise.all([
      page.waitForResponse(
        (r: Response) => r.url().includes('/confirm-import/') && r.request().method() === 'POST',
      ),
      page.locator('#confirmImportBtn').click(),
    ]);
    expect(confirmDialogMessage).toContain('6 transactions');
    expect(confirmDialogMessage).not.toContain('WARNING: Balance mismatch');

    // confirm_import returns HTTP 200 even on rejection ({"status": "error"}), so
    // waitForResponse above resolves either way and a rejection never redirects --
    // assert success and exactly one job here, before waiting for a URL that a
    // rejection would never produce, so a rejected import fails immediately and
    // legibly instead of burning the full 60s timeout below.
    const confirmData = await confirmResponse.json();
    expect(confirmData.status).toBe('success');
    expect(confirmData.job_ids).toHaveLength(1);

    await page.waitForURL(/(\?tab=review|\/review\/)/, { timeout: 60_000 });

    // confirm_import's own redirect always lands on the FY detail page
    // (?tab=review), never on the review job detail page it just created
    // (review/views.py:2648) -- so the job id is read off its JSON response
    // instead, and the job detail page (the one that actually renders
    // [data-txn-id] rows, per Ruling 18) is captured for the next test to visit.
    const [jobId] = confirmData.job_ids;
    reviewJobUrl = `${instance.baseURL}/review/${jobId}/`;

    await page.context().close();
  });

  test(`${opts.profile}: the debit and credit signs survive into the review queue`, async ({ browser }) => {
    // The sign is encoded only in the statement's column geometry -- nothing in
    // the text says debit or credit -- so this is what proves the columns were
    // read rather than guessed.
    const page = await seniorPage(browser);
    await page.goto(reviewJobUrl);
    const rows = page.locator('[data-txn-id]');
    await expect(rows.filter({ hasText: 'EFTPOS SALES INV 1001' })).toHaveAttribute(
      'data-amount', '1100.00',
    );
    await expect(rows.filter({ hasText: 'OFFICE SUPPLIES PTY LTD' })).toHaveAttribute(
      'data-amount', '-550.00',
    );
    await page.context().close();
  });

  // The brief's tax types ('GST', 'FRE') are not valid <option> values --
  // review_detail.html:635-642 enumerates exactly the seven below, and
  // selectOption would throw against anything else. The real vocabulary
  // encodes income-vs-expense direction (confirm_transaction,
  // review/views.py:661-663, maps it through canonical_tax_type), which is the
  // whole basis of the BAS's 1A/1B and G1/G11 split, so it isn't a detail to
  // paper over with the brief's flatter codes. Direction here is read off the
  // sign make_cba.py:69-74 draws into each row (credit => income, debit =>
  // expense); FRESH FOOD SUPPLIES and EXPORT SALE INV 1003 are GST-free on
  // their respective sides, matching the design doc's arithmetic
  // (G1 4,100.00 / 1A 300.00 / G11 872.00 / 1B 52.00 / net 248.00).
  //
  // The account codes are NOT the entity's own fixture chart (0510/1520/1530/
  // 1540, core/e2e_fixture_data.py's BANK_BAS_CHART). review/views.py:553-555
  // sources the picker from the global, entity-type-scoped ChartOfAccount
  // template instead -- confirmed against the E2E template database, not
  // guessed -- and two of those fixture codes (1520, 1530) don't exist there
  // at all.
  //
  // Every code below was chosen, and verified against the database, for
  // carrying NO mapped tax_code (0602, 0578, 1126 have none; 1545 and 1685
  // carry 'FOA', which is absent from taxCodeToTaxType's map,
  // review_detail.html:853-867). That is deliberate, not incidental: when an
  // account's tax_code DOES resolve (0510 Sales, tax_code GST, was the first
  // choice here), clicking its option fires TWO concurrent writes to the same
  // transaction -- selectAccount's own confirm (tryAutoConfirm, :937) AND a
  // parallel call to /gst-treatment/ (applyAccountGST, :873-885, hitting
  // set_gst_treatment, review/views_enhanced.py:558-608). That second endpoint
  // loads the row with a plain get_object_or_404 -- no select_for_update, no
  // atomic block -- and its own unconditional txn.save() (:599) can overwrite
  // is_confirmed/posted_to_tb back to their pre-confirm values if its stale
  // read lands before confirm_transaction's commit and its save lands after.
  // Measured on real runs, not assumed: with 0510 in this table, the suite
  // failed intermittently (fail/pass/fail across three runs) with the
  // transaction silently un-confirmed and its posting missing from the TB.
  // This is a live, unsynchronized-write bug in the application (a third
  // defect, distinct from the wrong-chart sourcing above and the posted_to_tb
  // guard issue elsewhere), tracked separately and NOT fixed here. With every
  // account below unmapped, applyAccountGST returns early (:878) before ever
  // reaching /gst-treatment/, so selectAccount's click never does more than
  // populate the picker -- confirming is left entirely to this loop's own
  // .selectOption(), and the race cannot occur. Consequence, documented for
  // Task 9: this suite now never exercises the auto-apply path at all.
  // The account NAMES for three of these (Filing fees, Net foreign income,
  // Goods for own use, Other non-operating revenue) are semantically
  // arbitrary -- the global company chart has no office-supplies or food
  // account -- chosen only for their tax_code, not their bookkeeping fit.
  // What this test pins is the tax treatment and the resulting BAS figures,
  // not account realism.
  const ALLOCATIONS: Array<[string, string, string]> = [
    ['EFTPOS SALES INV 1001', '0602', 'GST on Income'],
    ['OFFICE SUPPLIES PTY LTD', '1685', 'GST on Expenses'],
    ['BANK FEES AND CHARGES', '1545', 'GST on Expenses'],
    ['CONSULTING FEE INV 1002', '0602', 'GST on Income'],
    ['FRESH FOOD SUPPLIES', '1126', 'GST Free Expenses'],
    ['EXPORT SALE INV 1003', '0578', 'GST Free Income'],
  ];

  test(`${opts.profile}: every transaction allocates and posts to the trial balance`, async ({ browser }) => {
    const page = await seniorPage(browser);
    await page.goto(reviewJobUrl);

    // The page auto-starts AI classification 500ms after load whenever not every
    // transaction arrived auto-coded (the IIFE at review_detail.html:1976-1979),
    // and its async updates overwrite any row's account-picker input that hasn't
    // been given a dataset.code yet (updateTransactionRow, :1823-1825) -- which
    // includes the moment right after this loop's own .fill(code) below, since
    // fill() only sets .value, and dataset.code isn't set until the .account-
    // option click actually lands. Racing that window silently clobbered a row's
    // manually-typed code with the AI's own suggestion on an earlier run of this
    // test. #btn-classify is left disabled either way classification finishes --
    // classifyComplete() disables it (:1789) once the async batches drain, and the
    // auto-start IIFE's own else-branch disables it immediately if every
    // transaction already arrived auto-coded (:1978) -- so waiting for disabled
    // is the one condition that's a deterministic fix on both paths, not a sleep,
    // since classification's duration (metered, several seconds here) is not this
    // test's to control.
    await expect(page.locator('#btn-classify')).toBeDisabled({ timeout: 60_000 });

    for (const [description, code, taxType] of ALLOCATIONS) {
      const row = page.locator('[data-txn-id]').filter({ hasText: description });
      // The account control is a filter-as-you-type picker, not a <select>:
      // .account-picker-input narrows THIS ROW'S OWN .account-dropdown of
      // .account-option divs (review_detail.html:619-629, one dropdown <div>
      // per transaction), and clicking one calls selectAccount(txnId, code,
      // name). Two of these six rows share code 0602 (Other non-operating
      // revenue) -- scoping the option click to `row` rather than the whole
      // page matters: showDropdown (:812) sets a row's dropdown innerHTML but
      // hideAllDropdowns (:845) never clears it, only drops the .show class,
      // so once EFTPOS's row has rendered a 0602 option, that (now-hidden)
      // element is still in the DOM when CONSULTING FEE later renders its
      // own. A page-wide `.account-option[data-code="0602"]` would match
      // both, `.first()` in DOM order would grab EFTPOS's invisible leftover,
      // and the click would time out against an element that can never
      // become actionable again.
      await row.locator('.account-picker-input').fill(code);
      await row.locator(`.account-option[data-code="${code}"]`).first().click();
      // None of these six accounts carry a mapped tax_code (see the
      // ALLOCATIONS comment for why that's deliberate), so the click above
      // never does more than populate the picker -- this selectOption is the
      // only thing that actually confirms and posts this row.
      await row.locator('.tax-select').selectOption(taxType);
      // confirmTransaction's success handler (:958) sets data-confirmed on the
      // row directly from the fetch response, client-side, immediately -- no
      // reload needed (the brief's fallback wasn't required for this page).
      await expect(row).toHaveAttribute('data-confirmed', 'true');
    }

    await expect(page.locator('#confirmed-count')).toHaveText('6');
    // #btn-submit is disabled until every row is confirmed (:410), so this
    // proves the count above is what the page believes too. submitReview()
    // (:1720) opens a native confirm() before it ever fetches, the same
    // pattern the upload test above uses for #confirmImportBtn -- an
    // unhandled dialog would auto-dismiss and silently no-op the submit.
    let submitDialogMessage = '';
    page.once('dialog', (dialog: any) => {
      submitDialogMessage = dialog.message();
      dialog.accept();
    });
    const [submitResponse] = await Promise.all([
      page.waitForResponse(
        (r: Response) => r.url().includes('/submit/') && r.request().method() === 'POST',
      ),
      page.locator('#btn-submit').click(),
    ]);
    expect(submitDialogMessage).toContain('Submit this review');
    // Not submitResponse.json(): the success handler's own .then() navigates
    // the page away (review_detail.html:1728, window.location.href) the
    // instant it parses the body, and that can beat this script back to the
    // response object -- CDP then reports "No resource with given identifier
    // found" for a body it already discarded. submit_review only ever returns
    // {"status": "ok"} on an implicit 200; every rejection (unconfirmed rows,
    // review/views.py:818-822) is an explicit 400 -- so the status code alone
    // already distinguishes them, without racing the navigation for the body.
    expect(submitResponse.status()).toBe(200);

    await page.context().close();
  });

  test(`${opts.profile}: re-confirming a transaction does not post it twice`, async ({ browser }) => {
    /**
     * confirm_transaction (review/views.py:651-655) takes select_for_update on
     * the PendingTransaction row and re-checks posted_to_tb (:694) before
     * calling _post_confirmed_txn_to_tb -- and that helper's own centralised
     * core (core/views.py:1058, _post_txn_to_tb) carries a second, independent
     * posted_to_tb guard. This test asserts the guard's observable effect
     * rather than either mechanism: after re-confirming an already-posted
     * transaction, the trial balance figures must be unchanged. If both guards
     * were removed, _post_txn_to_tb would re-add BANK FEES AND CHARGES's net
     * and GST amounts to their existing TB lines (the `if not created` branch,
     * core/views.py:1084-1095, increments rather than sets), moving
     * #tb-header-debit without moving #tb-header-credit (the bank contra is
     * recalculated from confirmed+posted transactions, core/views.py:10571-
     * 10576, which is unaffected by a duplicate post) -- so this test would
     * fail the moment the guard broke, not pass regardless of it.
     */
    const page = await seniorPage(browser);
    await page.goto(`${instance.baseURL}/years/${FY}/`);
    const before = {
      debit: await page.locator('#tb-header-debit').textContent(),
      credit: await page.locator('#tb-header-credit').textContent(),
    };

    await page.goto(reviewJobUrl);
    const row = page.locator('[data-txn-id]').filter({ hasText: 'BANK FEES AND CHARGES' });
    // Code 1545 (Bank fees & charges), not the fixture's own 1530 -- see the
    // ALLOCATIONS comment above for why the picker can't reach 1530 at all.
    await row.locator('.account-picker-input').fill('1545');
    // .tax-select already renders "GST on Expenses" selected (confirmed_tax_type
    // from the previous test's confirm), so re-picking the account alone is
    // enough: tryAutoConfirm (:937) fires confirmTransaction() as soon as both
    // the input's data-code and the select's existing value are non-empty --
    // this re-triggers the exact endpoint the guard protects, without touching
    // .tax-select again. 1545's tax_code (FOA) is outside taxCodeToTaxType's
    // map, so applyAccountGST doesn't fire on this click either -- the only
    // confirm this triggers is the one the test means to send.
    const [confirmResponse] = await Promise.all([
      page.waitForResponse(
        (r: Response) => r.url().includes('/confirm/') && r.request().method() === 'POST',
      ),
      row.locator('.account-option[data-code="1545"]').first().click(),
    ]);
    const confirmData = await confirmResponse.json();
    expect(confirmData.status).toBe('ok');

    await page.goto(`${instance.baseURL}/years/${FY}/`);
    await expect(page.locator('#tb-header-debit')).toHaveText(before.debit ?? '');
    await expect(page.locator('#tb-header-credit')).toHaveText(before.credit ?? '');

    await page.context().close();
  });

  test(`${opts.profile}: the trial balance still balances after posting`, async ({ browser }) => {
    // Absolute form -- total debits equal total credits -- rather than pinning
    // either figure here: that holds under whatever posting convention
    // _post_txn_to_tb uses and needs no guess about GST stripping, and leaves
    // the per-account amounts to be pinned by G1/G11 in Task 7.
    const page = await seniorPage(browser);
    await page.goto(`${instance.baseURL}/years/${FY}/`);
    const debitText = await page.locator('#tb-header-debit').textContent();
    const creditText = await page.locator('#tb-header-credit').textContent();
    const num = (s: string | null) => parseFloat((s ?? '').replace(/[^0-9.-]/g, ''));
    expect(num(debitText)).toBeCloseTo(num(creditText), 2);

    await page.context().close();
  });
}
