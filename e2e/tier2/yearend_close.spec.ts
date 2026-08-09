import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import { execFile } from 'child_process';
import { promisify } from 'util';
import { startInstance, type Instance } from '../fixtures/instance';
import { loadUsers, loginAs } from '../fixtures/login';
import { dumpFigures, compareToBaseline, recordObserved } from '../fixtures/figures';
import { E2E_STATE_DIR, REPO_DIR, VENV_PYTHON } from '../fixtures/paths';

const execFileAsync = promisify(execFile);

/**
 * Year-end close: trial balance import through to posted depreciation.
 *
 * The assertions below are the contracts these views state in their own docstrings —
 * a TB out of balance beyond $0.02 is refused, depreciation posting is idempotent,
 * opening balances are never touched — so a failure means the code stopped honouring
 * something it claims about itself.
 */

const PORT = 8201;
const IDS = JSON.parse(fs.readFileSync(`${E2E_STATE_DIR}/fixture_entity.json`, 'utf-8'));
const FY = IDS.current_fy;
const TB_DIR = `${E2E_STATE_DIR}/tb`;

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

/**
 * Retag the imported accumulated-depreciation opening balance as a rollover line.
 *
 * This is the remediation depreciation_post_to_tb's refusal message asks the
 * accountant for. The fixture's opening TB carries $4,000 of accumulated
 * depreciation on a `tb_import` line; the handler's current-movement query excludes
 * only `rollover`, so until it is retagged that prior-year balance reads as
 * current-year movement with no expense-side counterpart.
 */
async function retagAccumDepOpeningAsRollover(dbName: string): Promise<void> {
  const script = `
from core.models import TrialBalanceLine
updated = TrialBalanceLine.objects.filter(
    financial_year_id=${JSON.stringify(FY)},
    account_code="1-2100",
    source="tb_import",
).update(source="rollover")
assert updated == 1, f"expected exactly one accum-dep import line, retagged {updated}"
`;
  await execFileAsync(
    VENV_PYTHON,
    ['manage.py', 'shell', '-c', script],
    {
      cwd: REPO_DIR,
      env: {
        ...process.env,
        DJANGO_SETTINGS_MODULE: 'config.settings_e2e',
        E2E_DB_NAME: dbName,
      },
    },
  );
}

test('posting depreciation is refused while the accumulated-depreciation opening sits on a current-year line', async ({
  browser,
}) => {
  const page = await accountantPage(browser);

  const before = await dumpFigures(instance.dbName, FY, 'before_asymmetric_dep_post');

  // The pair is asymmetric: $4,000 of accumulated-depreciation movement against no
  // depreciation-expense movement. Backing both sides out cannot produce a balanced
  // journal, so the handler must write nothing rather than post a single-sided one.
  expect(await postDepreciationViaModal(page)).toBeLessThan(400);

  const after = await dumpFigures(instance.dbName, FY, 'after_asymmetric_dep_post');
  expect(
    JSON.stringify(after),
    'a refused depreciation post must not change a single figure',
  ).toBe(JSON.stringify(before));
  expect(after.journals, 'a refused depreciation post must write no journal').toEqual([]);

  // The accountant has to be told which account is wrong, or the refusal is a dead end.
  await expect(page.locator('body')).toContainText('1-2100');

  await page.context().close();
});

test('posting depreciation is idempotent and leaves opening balances alone', async ({
  browser,
}) => {
  const page = await accountantPage(browser);

  // Apply the remediation the refusal above asks for, so this test exercises the
  // real post-and-repost path rather than passing because nothing happened.
  await retagAccumDepOpeningAsRollover(instance.dbName);

  const before = await dumpFigures(instance.dbName, FY, 'before_depreciation_post');

  // Both presses go through the real modal (see postDepreciationViaModal) so this
  // test proves the idempotency contract the way an accountant would actually
  // trigger it -- open the preview, click Confirm Post -- not via a hand-built
  // request that only resembles what the button sends.
  expect(await postDepreciationViaModal(page)).toBeLessThan(400);
  const first = await dumpFigures(instance.dbName, FY, 'after_depreciation_post');

  // Recorded before the assertions below rather than after, so the figures land in
  // the observed dump even on a run where one of them throws -- a checkpoint
  // captured only once everything has passed is never captured at all on a failing
  // run, which is exactly when it is wanted.
  //
  // Recorded, not yet compared. These figures are now honest -- the pair is
  // symmetric once the opening balance is retagged, so the reversal balances and
  // the accounts land on the schedule total -- but the checkpoint has no baseline
  // entry until someone blesses it, and comparing against an unblessed checkpoint
  // can only ever report "not in the baseline yet". Run `npm run bless:figures` to
  // promote it, then add the compare.
  recordObserved('after_depreciation_post', first);

  expect(await postDepreciationViaModal(page)).toBeLessThan(400);
  const second = await dumpFigures(instance.dbName, FY, 'after_depreciation_post_twice');

  // The docstring promises "on repeat presses with an unchanged schedule the net
  // change is zero". Stacking is the classic failure here.
  //
  // Compared as account positions rather than as raw rows: each press appends its
  // reversal and its re-post rather than rewriting history, so the TB line list and
  // the journal audit trail both grow by design, and their absolute totals grow with
  // them. What the contract fixes is where each account nets out. (core's
  // test_repeat_press_leaves_every_trial_balance_row_unchanged pins the same
  // property directly against the ORM.)
  const positions = (figures: any): Record<string, number> => {
    const net: Record<string, number> = {};
    for (const row of figures.trial_balance) {
      net[row.account_code] =
        (net[row.account_code] ?? 0) + Number(row.debit) - Number(row.credit);
    }
    return net;
  };
  expect(
    positions(second),
    'pressing post-to-TB twice must not move any account',
  ).toEqual(positions(first));
  expect(
    second.depreciation,
    'pressing post-to-TB twice must not change the depreciation schedule',
  ).toEqual(first.depreciation);
  expect(
    second.totals.debit,
    'the trial balance must still balance after a repeat press',
  ).toBe(second.totals.credit);

  // Summed per account for the same reason the positions check above is: posting
  // adds rows, and a row-by-row comparison would report every added row as a
  // difference. Summing keeps the check strict -- an altered opening balance on an
  // existing row moves its account's total, and so does a newly added row carrying
  // a non-zero opening -- while tolerating the rows that legitimately appear.
  const openings = (figures: any): Record<string, number> => {
    const total: Record<string, number> = {};
    for (const row of figures.trial_balance) {
      total[row.account_code] =
        (total[row.account_code] ?? 0) + Number(row.opening_balance);
    }
    return total;
  };
  // Compared over the union of account codes, defaulting to zero. Posting
  // introduces account codes that had no TB row at all (the fixture carries no
  // 6-1200 Depreciation row until the schedule posts one), and _apply_journal_line_to_tb
  // creates every row with opening_balance 0 — so an account appearing for the first
  // time must contribute nothing. Defaulting rather than intersecting keeps that
  // strict: a new row arriving with a non-zero opening fails here, where an
  // intersection would quietly skip it.
  const beforeOpenings = openings(before);
  const afterOpenings = openings(first);
  const allCodes = [
    ...new Set([...Object.keys(beforeOpenings), ...Object.keys(afterOpenings)]),
  ].sort();
  const normalise = (totals: Record<string, number>) =>
    Object.fromEntries(allCodes.map((code) => [code, totals[code] ?? 0]));
  expect(
    normalise(afterOpenings),
    'posting depreciation must not touch opening balances',
  ).toEqual(normalise(beforeOpenings));

  // Every journal this handler posts must balance, so the trial balance it leaves
  // behind must too. This is the assertion the single-sided reversal used to fail:
  // it left 120,000 debit against 116,000 credit on the very first press, before a
  // second press ever stacked anything on top.
  expect(first.totals.debit).toBe(first.totals.credit);

  await page.context().close();
});
