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

  // Row-scoped accessor for the GST Activity Statement's summary tab
  // (templates/core/gst_activity_statement.html:273-360, `#summaryTab`, the
  // default-active tab per `:273`'s `show active`). Locating a label by
  // substring is unsafe on this page: "G1" is a substring of "G10", "G11",
  // "G12" and "G19", which is exactly the trap in the brief's prescribed
  // num() regex helper (Ruling 28) -- so this matches the row's FIRST cell
  // (`tr > td:first-child`, the label column, never the description or
  // amount columns) against the label with anchors (`^...$`), an exact
  // match rather than a prefix match. Scoping to `#summaryTab` also matters
  // on its own: `#basDetailTab` (:363) stays in the DOM while hidden and
  // renders "Total Sales (G1)" at :527, which a page-wide search would also
  // see. `toHaveCount(1)` is asserted before reading anything, so a label
  // that is missing or ambiguous fails loudly here instead of a `.first()`
  // or bare `textContent()` silently reading the wrong row.
  async function basRow(page: any, label: string) {
    const labelCell = page
      .locator('#summaryTab tr > td:first-child')
      .filter({ hasText: new RegExp(`^${label}$`) });
    await expect(
      labelCell,
      `expected exactly one #summaryTab row labelled "${label}"`,
    ).toHaveCount(1);
    return labelCell.locator('xpath=ancestor::tr[1]');
  }

  // The row's last <td> is always the amount cell (label, description,
  // amount -- gst_activity_statement.html:284, :311), whatever the row's
  // exact column count, so `.last()` rather than a fixed index -- this also
  // covers the net row below, which has only two cells (colspan="2" label,
  // then amount). Parsed to a number (stripping "$" and any thousands
  // separator) rather than compared as a literal string: USE_THOUSAND_SEPARATOR
  // is unset in this project's settings, so floatformat:2 may or may not
  // group digits, and that formatting choice is not what this suite is
  // pinning -- the figure is. Shared by basValue and basNetValue below so the
  // two accessors can't drift apart on how a cell's text becomes a number.
  async function readAmount(row: any): Promise<number> {
    const text = (await row.locator('td').last().textContent()) ?? '';
    return parseFloat(text.replace(/[^0-9.-]/g, ''));
  }

  async function basValue(page: any, label: string): Promise<number> {
    const row = await basRow(page, label);
    return readAmount(row);
  }

  // The net-GST row (gst_activity_statement.html:350-355) is shaped
  // differently from every other summary row: its first cell is a
  // colspan="2" label whose text is sign-dependent -- "Net GST Payable to
  // ATO" when gst_payable > 0, "Net GST Refundable from ATO" otherwise
  // (:352) -- so it cannot be found by basRow's exact-label match and needs
  // its own accessor. No other row in #summaryTab carries a colspan, so the
  // attribute alone identifies it regardless of which wording rendered.
  async function basNetValue(page: any): Promise<number> {
    const labelCell = page.locator('#summaryTab tr > td:first-child[colspan="2"]');
    await expect(labelCell, 'expected exactly one #summaryTab net-GST row').toHaveCount(1);
    const row = labelCell.locator('xpath=ancestor::tr[1]');
    return readAmount(row);
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
  // review_detail.html:636-642 enumerates exactly the seven below, and
  // selectOption would throw against anything else. The real vocabulary
  // encodes income-vs-expense direction (confirm_transaction,
  // review/views.py:661-663, maps it through canonical_tax_type), which is the
  // whole basis of the BAS's 1A/1B and G1/G11 split, so it isn't a detail to
  // paper over with the brief's flatter codes. Direction here is read off the
  // sign make_cba.py:68-73 draws into each row (credit => income, debit =>
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
  // parallel call to /gst-treatment/ (applyAccountGST, :873-912, the fetch
  // itself at :896, hitting set_gst_treatment, review/views_enhanced.py:
  // 558-608). That second endpoint loads the row with a plain
  // get_object_or_404 -- no select_for_update, no atomic block -- and its
  // own unconditional txn.save() (:599) can overwrite
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
    // transaction arrived auto-coded (the IIFE at review_detail.html:1976-1979).
    // #btn-classify going disabled is NOT a "finished" signal: startClassification()
    // disables it the INSTANT classification starts (:1746, text "Classifying..."),
    // so toBeDisabled() alone is satisfied immediately and proves nothing about
    // completion -- a real, measured defect in an earlier version of this test.
    // classifyComplete() is the only function that ever writes the exact text
    // "Classification Complete" (:1787), once the async batches genuinely drain,
    // so wait for that specifically. classifyError() (:1793-1800) re-enables the
    // button with different text ("Retry Classification") on failure, so an AI
    // outage fails this wait loudly once the 60s budget (metered AI step) expires,
    // rather than hanging forever or silently passing on a stale state.
    await expect(page.locator('#btn-classify')).toHaveText(
      /Classification Complete/, { timeout: 60_000 },
    );

    // Genuine completion is not sufficient on its own either: updateTransactionRow
    // (:1826) sets every row's .tax-select.value to the AI's own suggested
    // tax_type client-side as each batch lands (the select only pre-selects from
    // confirmed_tax_type server-side, :636-642, which is still empty pre-confirm
    // -- so without classification the select's DOM value is genuinely blank, but
    // classification fills it in regardless of whether anyone accepts the
    // suggestion). That means the instant this loop's account-option click lands,
    // selectAccount (:928) -> tryAutoConfirm (:943) finds BOTH the input's
    // dataset.code and the AI-supplied tax-select value non-empty and fires
    // confirmTransaction with the AI's tax type -- before this loop's own
    // .selectOption() ever runs. That confirm posts to the TB and sets
    // posted_to_tb (:694-695); the explicit .selectOption() below then only
    // updates the record, since confirm_transaction's guard skips reposting. So
    // without a reload here, what actually lands in the ledger -- the split
    // between the P&L account and the 3380 GST control line -- is decided by the
    // metered, non-deterministic AI, not by ALLOCATIONS; debits still equal
    // credits either way (the AI's own tax type still nets to a balanced
    // posting), which is why nothing else here would catch it. Reloading clears
    // it: the server re-renders account-picker-input from confirmed_code and
    // .tax-select from confirmed_tax_type (:625, :636-642), both still empty for
    // every row at this point, so after the reload the account click can no
    // longer auto-confirm. Crucially, this reload only runs after classification
    // has GENUINELY finished (the wait above), not merely started -- classify_batch
    // persists job.auto_coded_count to the database once a batch actually returns
    // (review/views.py:2056-2058), so reloading mid-run (as the disabled-only wait
    // used to allow) interrupts that persistence, leaves auto_coded_count below
    // total_transactions, and makes the auto-start IIFE (:1977) re-fire
    // classification on every later page load in this file -- which is exactly
    // what produced stray, metered AI calls and an intermittent posting loss on a
    // real run of this suite. Waiting for the real completion text closes that off.
    await page.reload();

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
      // Doubly true this click alone can't confirm the row: none of these six
      // accounts carry a mapped tax_code (see the ALLOCATIONS comment), so
      // applyAccountGST returns early and never touches .tax-select; and the
      // reload above means classification's own client-side pre-fill of
      // .tax-select is gone too (the select is genuinely blank again, per the
      // reload's own comment above). So tryAutoConfirm's tax-select check is
      // empty either way, and this selectOption is what actually confirms and
      // posts this row.
      await row.locator('.tax-select').selectOption(taxType);
      // confirmTransaction's success handler (:958) sets data-confirmed on the
      // row directly from the fetch response, client-side, immediately -- no
      // further reload needed to observe it.
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
     * confirm_transaction (review/views.py:653-655) takes select_for_update on
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

    // The equal-totals check above can't catch a partial posting loss:
    // _recalc_bank_contra (core/views.py:10571-10600) derives the bank line
    // from confirmed+posted transactions and SETS it from scratch, so both
    // header totals stay equal whatever the per-account split -- and stay
    // equal even if one of the six transactions' posting were silently
    // dropped (measured directly: an earlier version of the classification
    // wait above let a page reload interrupt classification mid-run, which
    // did exactly this to a real trial balance -- fixed above, not by
    // anything here). Task 7 won't close this gap either:
    // _confirmed_transactions (core/bas_utils.py:543) filters on
    // is_confirmed=True and never on posted_to_tb, so the BAS would happily
    // report a transaction whose TB posting never landed. Pin the bank
    // account's own line instead -- NOT as separate gross Dr/Cr figures
    // (financial_year_detail.html:811-812 render line.display_dr/display_cr,
    // but core/views.py:1777-1787 derives both from the account's single
    // signed closing_balance, not from raw debit/credit turnover, so one
    // account can never show a nonzero figure in both columns at once,
    // confirmed against a live run: this account rendered 3,228.00 in the Dr
    // cell and nothing in the Cr cell, never "4,100.00 / 872.00" as two
    // simultaneous cells). The net closing balance itself still catches a
    // lost posting just as well, and it is ground truth rather than a
    // baseline: 1100 + 2200 + 800 - 550 - 22 - 300 = 3,228.00, independently
    // derived from the statement's own transactions, and it is exactly Task
    // 1's own statement reconciliation total -- not a figure this test is
    // the first to produce. Dropping any one of the six changes it.
    const bankRow = page
      .locator('#tb-table code', { hasText: /^\s*2000\s*$/ })
      .locator('xpath=ancestor::tr[1]');
    const bankCells = bankRow.locator('td');
    await expect(bankCells.nth(2)).toHaveText('3,228.00');
    await expect(bankCells.nth(3)).toHaveText('');

    await page.context().close();
  });

  test(`${opts.profile}: the BAS labels equal their hand-computed values`, async ({ browser }) => {
    /**
     * From the fixture's transaction table, GST at 1/11 of the GST-inclusive
     * amount:
     *
     *   G1  total sales incl GST      1,100 + 2,200 + 800 = 4,100.00
     *   G2  export sales                                     0.00
     *   G3  other GST-free sales                            800.00
     *   G10 capital purchases                                 0.00
     *   G11 non-capital purchases     550 + 22 + 300      =  872.00
     *   1A  GST on sales              (1,100 + 2,200)/11  =  300.00
     *   1B  GST on purchases          (550 + 22)/11       =   52.00
     *   net                           300.00 - 52.00      =  248.00
     *
     * The export sale (EXPORT SALE INV 1003) is expected in G3, not G2: it is
     * allocated tax type "GST Free Income" (ALLOCATIONS above), which
     * normalise_tax_treatment (core/bas_utils.py:266) maps to tax code "FRE",
     * and _classify_line's revenue branch (core/bas_utils.py:400-402) buckets
     * any GST-free (or untagged) revenue line into G3 -- this engine has no
     * path that ever populates G2 from a bank-statement transaction. If the
     * application disagrees with any figure here, STOP and report which label
     * and by how much -- these are hand-derived from the fixture, not a
     * baseline, and are not to be edited to match a different output.
     *
     * G2's expected 0.00 is a STRUCTURAL constant, not a fixture-derived one:
     * g_totals["G2"] is zero-initialized (core/bas_utils.py:592, the G1..G20
     * dict comprehension) and then only ever READ (:856, G5 = G2+G3+G4; :872,
     * copied into bas_data) -- _classify_line (:361-440) has no branch that
     * ever writes "G2" into its contributions dict, so G2 renders 0.00 for
     * every fixture, not just this one. The assertion still earns its place
     * (it catches the row disappearing from the template, or the engine one
     * day gaining a G2 path), but it is not evidence about our data the way
     * the other seven figures are. Contrast G10's 0.00 just below: that one IS
     * genuinely fixture-dependent -- _classify_line's expenses branch
     * (core/bas_utils.py:407-412) can populate G10 from a capital-tax-coded
     * purchase, and this fixture's six transactions simply don't produce one.
     */
    const page = await seniorPage(browser);
    await page.goto(`${instance.baseURL}/years/${FY}/gst/`);

    expect(await basValue(page, 'G1')).toBeCloseTo(4100.0, 2);
    expect(await basValue(page, 'G2')).toBeCloseTo(0, 2);
    expect(await basValue(page, 'G3')).toBeCloseTo(800.0, 2);
    expect(await basValue(page, 'G10')).toBeCloseTo(0, 2);
    expect(await basValue(page, 'G11')).toBeCloseTo(872.0, 2);
    expect(await basValue(page, '1A')).toBeCloseTo(300.0, 2);
    expect(await basValue(page, '1B')).toBeCloseTo(52.0, 2);
    expect(await basNetValue(page)).toBeCloseTo(248.0, 2);

    await page.context().close();
  });

  test(`${opts.profile}: the GST identity holds`, async ({ browser }) => {
    // 1A - 1B = net, whatever the individual figures turn out to be. An
    // absolute relationship needs no baseline: if it fails, the figures are
    // wrong regardless of what was asserted as the hand-computed value above.
    // Uses the same row-scoped accessors as that test, not the brief's
    // prefix-matching num() regex (Ruling 28).
    //
    // Honestly: for THIS fixture this test is redundant, not independent
    // coverage. It reads 1A, 1B and net through the exact same accessors the
    // preceding test already pins to 300, 52 and 248, so 300 - 52 ≈ 248
    // cannot fail while that test passes -- it is not exercising a code path
    // the other test doesn't already touch. It earns its keep as an
    // invariant that would catch a REAL divergence the moment any profile
    // parameterises these constants (a future fixture where 1A/1B/net are
    // not hand-picked to satisfy the identity by construction), which is why
    // it stays rather than being deleted.
    const page = await seniorPage(browser);
    await page.goto(`${instance.baseURL}/years/${FY}/gst/`);

    const oneA = await basValue(page, '1A');
    const oneB = await basValue(page, '1B');
    const net = await basNetValue(page);
    expect(oneA - oneB).toBeCloseTo(net, 2);

    await page.context().close();
  });

  // ─── The coverage gate, lodgement and unlodge ────────────────────────────
  //
  // The fixture's six transactions all land in October, one calendar month of
  // a three-month quarter. October's quarter -- Q2 of a 1 July financial year
  // -- is therefore permanently "partial" coverage: two of its three months
  // never have a statement. LODGE_PERIOD names that period's number, which is
  // what `?period=` on the BAS dashboard selects (core/views_bas.py:95,
  // :103-107) -- used below instead of a bare `2` so its meaning is stated once.
  const LODGE_PERIOD = 2;

  async function officeAdminPage(browser: any) {
    const users = loadUsers();
    const context = await browser.newContext({ baseURL: instance.baseURL });
    const page = await context.newPage();
    await loginAs(page, users.roles.office_admin, users.password);
    return page;
  }

  test(`${opts.profile}: lodgement is blocked while bank coverage is incomplete`, async ({ browser }) => {
    /**
     * core/views_bas.py:252 refuses to lodge unless get_bank_coverage reports
     * complete, or an override reason is supplied -- but that refusal is not
     * reachable through the UI as the brief originally assumed: the plain
     * lodge form is rendered ONLY for a 'ready' period
     * (gst_activity_statement.html:200-204, the button targeting
     * `#lodgeModal`). This fixture's period is 'partial' (one of three months
     * present), so the template's `{% elif %}` chain (:200/:205/:212) never
     * reaches that branch at all -- the only lodge control offered is the
     * override-carrying "Lodge with Warning" button (:205-211, targeting
     * `#lodgePartialModal`). Both assertions matter together: the presence of
     * the override path is only informative once the plain path is confirmed
     * gone too -- a regression that silently brought back the ungated button
     * would otherwise pass unnoticed by a test that only checks what IS there.
     */
    const page = await seniorPage(browser);
    await page.goto(`${instance.baseURL}/years/${FY}/gst/?period=${LODGE_PERIOD}`);

    await expect(
      page.locator('button[data-bs-target="#lodgeModal"]'),
      'the plain (unconditional) lodge trigger must not render for a partial period',
    ).toHaveCount(0);

    const partialTrigger = page.locator('button[data-bs-target="#lodgePartialModal"]');
    await expect(
      partialTrigger,
      'the override-carrying lodge trigger must be the only lodge control offered',
    ).toHaveCount(1);
    await expect(partialTrigger).toContainText('Lodge with Warning');

    // gst_activity_statement.html:209-211, the only place "Missing:" renders.
    await expect(page.locator('div.text-danger.mt-1')).toContainText('Missing:');

    await page.context().close();
  });

  test(`${opts.profile}: an override reason lets it lodge and freezes the figures`, async ({ browser }) => {
    /**
     * Drives the real override path: open #lodgePartialModal -- the only modal
     * carrying `name="override_reason"` (:1036) -- fill it, and submit THAT
     * modal's own form. Two forms on this page post to bas_lodge_period for
     * this same period (:978 inside #lodgeModal, :1013 inside
     * #lodgePartialModal); #lodgeModal's has no override field at all, so
     * scoping the fill and the submit click to #lodgePartialModal is what
     * actually reaches the override branch rather than failing against the
     * wrong form.
     */
    const page = await seniorPage(browser);
    await page.goto(`${instance.baseURL}/years/${FY}/gst/?period=${LODGE_PERIOD}`);

    await page.locator('button[data-bs-target="#lodgePartialModal"]').click();
    await page.locator('#lodgePartialModal').waitFor({ state: 'visible' });
    await page.locator('#lodgePartialModal textarea[name="override_reason"]')
      .fill('e2e fixture: single month of quarter by design');

    const [lodgeResponse] = await Promise.all([
      page.waitForResponse(
        (r: Response) => r.url().includes('/gst/lodge/') && r.request().method() === 'POST',
      ),
      page.locator('#lodgePartialModal form[action*="/gst/lodge/"] button[type="submit"]').click(),
    ]);
    // A real (non-fetch) form submit navigates the browser through whatever
    // bas_lodge_period returns -- a redirect to the referer on success
    // (core/views_bas.py:284-290), which this response object reports as its
    // own hop rather than the page the browser lands on afterwards -- so
    // status alone (not body) is what's checked here, and the actual load is
    // awaited explicitly below before any assertion reads the resulting page.
    expect(lodgeResponse.status()).toBeLessThan(400);
    await page.waitForLoadState('load');

    // The strongest available proof the period is now genuinely 'lodged' as
    // this senior sees it: the Unlodge button is gated on BOTH
    // `selected_period.status == 'lodged'` AND `request.user.is_senior`
    // (gst_activity_statement.html:1053) -- so its appearance is not merely
    // "some lodged badge rendered", it is markup that cannot exist unless the
    // status transition actually committed. The previous test's two lodge
    // triggers disappearing corroborates the same transition from the other
    // direction: neither the 'ready' nor the 'partial' branch is selected now.
    await expect(page.locator('button[data-bs-target="#unlodgeModal"]')).toHaveCount(1);
    await expect(page.locator('button[data-bs-target="#lodgeModal"]')).toHaveCount(0);
    await expect(page.locator('button[data-bs-target="#lodgePartialModal"]')).toHaveCount(0);

    // The snapshot captured at lodge time (bp.snapshot_net, core/views_bas.py:
    // 271) should carry Task 7's hand-computed net figure. Read it off the
    // period status strip's own lodged-period rendering
    // (gst_activity_statement.html:119-122, "Net: $..."), scoped to the one
    // card carrying `border-dark border-2` -- the class combination the
    // template adds ONLY to the card matching the currently selected period
    // (:110), confirmed by grep to appear nowhere else in this template --
    // rather than matching "248.00" anywhere on the page, which is exactly
    // the vacuous-match failure mode Ruling 28 exists to rule out. Parsed to a
    // number, not compared as a literal string, for the same reason
    // readAmount() above is.
    const selectedStripCard = page.locator('.card.border-dark.border-2');
    await expect(
      selectedStripCard,
      'expected exactly one selected period card in the status strip',
    ).toHaveCount(1);
    // gst_activity_statement.html:120's own class list -- scoped this tightly
    // because a looser `div` + `hasText` filter also matches every ANCESTOR
    // div of this one (their combined textContent contains "Net:" too, since
    // hasText tests the whole subtree, not just this element's own text) --
    // measured directly: it returned 2 elements before this fix.
    const netLine = selectedStripCard.locator('div.text-muted.mt-1');
    await expect(netLine, 'expected exactly one "Net:" line on the selected period card').toHaveCount(1);
    const netText = (await netLine.textContent()) ?? '';
    expect(parseFloat(netText.replace(/[^0-9.-]/g, ''))).toBeCloseTo(248.0, 2);

    await page.context().close();
  });

  test(`${opts.profile}: a non-senior sees no unlodge control on a lodged period`, async ({ browser }) => {
    /**
     * Ruling 32 calls for this "as an accountant" -- but the accountant role
     * cannot reach this page at all. It needs an explicit entity assignment
     * to view any entity (core/e2e_support.py:80-90, `needs_assignments`), and
     * this fixture entity ("E2E Bank BAS Pty Ltd") never gets one:
     * e2e_bootstrap_users assigns its pool of PRE-EXISTING entities, and runs
     * strictly before e2e_seed_fixture_entity ever creates this one
     * (e2e/scripts/start_server.sh:54, :57) -- so at assignment time the
     * fixture entity does not exist yet and e2e_accountant can never be handed
     * it. Logging in as e2e_accountant here would 403 on
     * get_financial_year_for_user (config/authorization.py:107-122) before
     * ever reaching the template, which would prove nothing about the unlodge
     * control -- only that the entity is unassigned, a fact this test isn't
     * about.
     *
     * office_admin is the role that actually isolates the property this test
     * wants: can_view_all_entities includes it (accounts/models.py:59-62), so
     * it reaches this page the way an admin or senior would, but is_senior
     * does NOT (:55-57) -- so this is a genuine non-senior view of a lodged
     * period, the exact gap the unlodge button's own template guard checks
     * (gst_activity_statement.html:1053, `request.user.is_senior`).
     */
    const page = await officeAdminPage(browser);
    await page.goto(`${instance.baseURL}/years/${FY}/gst/?period=${LODGE_PERIOD}`);

    // Confirms this is genuinely the 'lodged' branch for this viewer, not
    // merely a page this role failed to render meaningfully: neither
    // pre-lodge trigger should be present either.
    await expect(page.locator('button[data-bs-target="#lodgeModal"]')).toHaveCount(0);
    await expect(page.locator('button[data-bs-target="#lodgePartialModal"]')).toHaveCount(0);

    await expect(
      page.locator('button[data-bs-target="#unlodgeModal"]'),
      'a non-senior user must not see the unlodge trigger on a lodged period',
    ).toHaveCount(0);

    await page.context().close();
  });

  test(`${opts.profile}: only a senior can unlodge, even by direct request`, async ({ browser }) => {
    /**
     * core/views_bas.py:304-306 is the only guard bas_unlodge_period carries
     * -- unlike bas_lodge_period it has no separate can_do_accounting check
     * ahead of it, so there is no risk of this request tripping a DIFFERENT
     * guard's message first (a real risk the brief flags for the lodge
     * endpoint, not this one).
     *
     * The test above only proves the button is hidden; it says nothing about
     * whether the endpoint itself would refuse a hand-crafted request. This
     * one drives that request directly, in-page fetch() (the same pattern
     * yearend_close.spec.ts:205-215 uses), reading a CSRF token off a form
     * already in this office_admin's own DOM -- #lodgePartialModal's, which
     * renders unconditionally whenever selected_period exists
     * (gst_activity_statement.html:969-1050), regardless of viewer role or
     * the period's current status, so it is a legitimate token this user's
     * own session actually holds, not one borrowed from elsewhere.
     *
     * Supplying a real unlodge_reason is deliberate: bp.status genuinely is
     * 'lodged' at this point (the previous test proved it), so if the
     * is_senior guard were ever removed, this exact request carries
     * everything bas_unlodge_period needs to actually unlodge the period.
     * That means this assertion would fail loudly -- the wrong message, and a
     * mutated period -- rather than passing regardless of whether the guard
     * exists, which is what a test asserting only a hidden button cannot do.
     */
    const page = await officeAdminPage(browser);
    await page.goto(`${instance.baseURL}/years/${FY}/gst/?period=${LODGE_PERIOD}`);

    const result = await page.evaluate(async ({ fy, period }) => {
      const token = (document.querySelector(
        '#lodgePartialModal [name="csrfmiddlewaretoken"]',
      ) as HTMLInputElement)?.value;
      const response = await fetch(`/years/${fy}/gst/unlodge/${period}/`, {
        method: 'POST',
        headers: { 'X-CSRFToken': token, 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({ unlodge_reason: 'e2e probe: office_admin should be refused' }),
        redirect: 'follow',
      });
      return { status: response.status, text: await response.text() };
    }, { fy: FY, period: LODGE_PERIOD });

    expect(result.status).toBeLessThan(400);
    expect(result.text).toContain(
      'Only senior accountants and administrators can unlodge BAS periods.',
    );
    // Belt-and-braces: a successful unlodge renders "has been unlodged"
    // instead (core/views_bas.py:332). Its absence here is further proof this
    // hit the refusal path, not a successful unlodge whose page happened to
    // also contain the word "senior" somewhere unrelated.
    expect(result.text).not.toContain('has been unlodged');

    await page.context().close();
  });
}
