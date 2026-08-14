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
}
