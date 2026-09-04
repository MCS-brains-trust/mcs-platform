import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import { REPO_DIR } from './paths';

/**
 * The import wizard's commit-button gate.
 *
 * Kinross Builders FY2026, 2026-09-04: 40 QuickBooks accounts were assigned to
 * entity chart accounts, every Statement Line visibly filled itself in, the
 * "Needs Mapping" counter read 0 -- and Confirm stayed grey. The mapping work
 * lives only in the DOM until the form submits, so this was 40 accounts of
 * unsaved work against a button that could not be pressed.
 *
 * checkUnmapped() is the only thing that enables or disables that button, and it
 * runs on page load and on the `change` event of a Statement Line select.
 * assignEntityAccount() fills the select with `select.value = mapsToId`, which
 * fires no `change` event, and then called updateCounts() alone. So the button
 * kept whatever state it had at page load -- disabled, because every row arrived
 * unmapped -- no matter how much the accountant mapped.
 *
 * This drives the real static/js/import_wizard.js against a real DOM through its
 * public assignEntityAccount(), because the defect is entirely in the wiring
 * between two functions. Testing the arithmetic of either one in isolation would
 * have passed throughout.
 *
 * It lives under the unit config, which exists to keep tests that need no Django
 * instance away from the tier rigs' 180s boot. This one does open a browser,
 * which that config's header does not anticipate -- but it needs no server, and
 * that is the property the separate config is protecting.
 */

const WIZARD_JS = fs.readFileSync(`${REPO_DIR}/static/js/import_wizard.js`, 'utf-8');

/** Two rows, both arriving unmapped, in a balanced trial balance. */
function harness(): string {
  const row = (idx: number, code: string, dr: string, cr: string) => `
    <tr class="mapping-row" data-idx="${idx}" data-source-code="${code}">
      <td class="account-name-cell">Source ${code}</td>
      <td class="entity-acct-cell">
        <input type="hidden" class="entity-acct-input" value="">
        <div class="entity-acct-display unassigned" data-idx="${idx}">Click to assign...</div>
      </td>
      <td><select class="mapping-select" data-learned-value=""><option value="">— Not mapped —</option></select></td>
      <td class="debit-cell">${dr}</td>
      <td class="credit-cell">${cr}</td>
    </tr>`;
  return `<!doctype html><html><body>
    <table><tbody>${row(0, '53', '1000.00', '0')}${row(1, '58', '0', '1000.00')}</tbody></table>
    <span id="mappedCount">0</span><span id="unmappedCount">2</span>
    <span id="unmappedWarning"></span>
    <span id="totalDebit"></span><span id="totalCredit"></span>
    <form id="importForm"><button id="commitBtn" disabled>Confirm</button></form>
    <script>${WIZARD_JS}</script>
  </body></html>`;
}

const CONFIG = {
  standardAccounts: [
    { id: 'sa-1', standard_code: 'BS001', line_item_label: 'Cash at bank', statement_section: 'Current Assets' },
    { id: 'sa-2', standard_code: 'BS002', line_item_label: 'Receivables', statement_section: 'Current Assets' },
  ],
  entityAccounts: [
    { code: '1100', name: 'Business Cheque Account', section: 'assets', maps_to_id: 'sa-1' },
    { code: '1200', name: 'Trade Debtors', section: 'assets', maps_to_id: 'sa-2' },
  ],
  entityPk: 'e', fyPk: 'f', suggestCodeUrl: '/s', quickAddUrl: '/q',
  csrfToken: 'x', balanceRequired: true,
};

async function boot(page) {
  await page.setContent(harness());
  await page.evaluate((cfg) => (window as any).ImportWizard.init(cfg), CONFIG);
}

test('the commit button starts disabled while rows are unmapped', async ({ page }) => {
  await boot(page);
  await expect(page.locator('#commitBtn')).toBeDisabled();
});

test('assigning entity accounts to every row enables the commit button', async ({ page }) => {
  await boot(page);

  await page.evaluate(() => {
    const w = (window as any).ImportWizard;
    w.assignEntityAccount('0', '1100', 'Business Cheque Account', 'sa-1');
    w.assignEntityAccount('1', '1200', 'Trade Debtors', 'sa-2');
  });

  // Every Statement Line is filled, so nothing is left to map.
  const selected = await page.evaluate(() =>
    [...document.querySelectorAll<HTMLSelectElement>('.mapping-select')].map(s => s.value));
  expect(selected).toEqual(['sa-1', 'sa-2']);

  await expect(page.locator('#commitBtn')).toBeEnabled();
});

test('a row with an entity account but no statement line can still be posted', async ({ page }) => {
  // commit_import's only hard gate is the Entity Account: "Cannot commit -- N
  // row(s) have no Entity Account (COA) assigned". The statement line feeds
  // mapped_line_item and the learning system and is explicitly optional there,
  // so blocking on it in the browser made the button stricter than the endpoint
  // it guards -- which is what stranded 40 mapped accounts behind a dead button.
  await boot(page);

  await page.evaluate(() => {
    const w = (window as any).ImportWizard;
    w.assignEntityAccount('0', '1100', 'Business Cheque Account', 'sa-1');
    w.assignEntityAccount('1', '9999', 'Unmapped Account', '');   // no maps_to
  });

  await expect(page.locator('#commitBtn')).toBeEnabled();
});

test('a row with no entity account still blocks, as the server would', async ({ page }) => {
  await boot(page);

  await page.evaluate(() => {
    (window as any).ImportWizard.assignEntityAccount('0', '1100', 'Business Cheque Account', 'sa-1');
  });

  // Row 1 has nothing. commit_import would reject the whole post, so the button
  // must stay down rather than let the accountant discover it on the round trip.
  await expect(page.locator('#commitBtn')).toBeDisabled();
});

test('the counter tracks what the button actually gates on', async ({ page }) => {
  await boot(page);

  await page.evaluate(() => {
    (window as any).ImportWizard.assignEntityAccount('1', '9999', 'Unmapped Account', '');
  });

  // One row assigned, one not. A counter that disagrees with the gate is what
  // put "Needs Mapping: 0" beside a dead button in the first place.
  await expect(page.locator('#unmappedCount')).toHaveText('1');
});
