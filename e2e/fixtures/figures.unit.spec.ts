import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { compareToBaseline } from './figures';

/**
 * Pure-logic coverage for compareToBaseline.
 *
 * The comparator is the only thing standing between a real regression and a green
 * run — everything else in this file just drives it. A comparator that silently
 * returned [] when a figure moved would make every Tier 2 assertion vacuous, so this
 * checks the shapes a diff has to notice: a changed figure, a row the baseline
 * expects but the run didn't produce, a row the run produced that the baseline
 * doesn't know about, a checkpoint the baseline has never seen at all, and two rows
 * colliding on the same account_code|source key (core/e2e_figures.py deliberately
 * permits this, which is why the comparator groups instead of overwriting).
 *
 * compareToBaseline's baseline path is injectable specifically so this file never has
 * to touch the real tier2/figures.baseline.json: a fixture is written to a scratch
 * directory under a random name and removed afterwards, so a crash mid-test can never
 * leave the real, committed baseline holding fake data.
 */

const SCRATCH_DIR = fs.mkdtempSync(path.join(os.tmpdir(), 'figures-unit-'));
const BASELINE_PATH = path.join(SCRATCH_DIR, 'figures.baseline.json');

test.afterAll(() => {
  fs.rmSync(SCRATCH_DIR, { recursive: true, force: true });
});

const ROW_CASH = {
  account_code: '1-1000',
  account_name: 'Cash',
  source: 'import',
  opening_balance: '0.00',
  debit: '100.00',
  credit: '0.00',
  closing_balance: '100.00',
  prior_closing_balance: '0.00',
  is_adjustment: false,
};

const ROW_DEPRECIATION = {
  account_code: '6-1200',
  account_name: 'Depreciation Expense',
  source: 'journal',
  opening_balance: '0.00',
  debit: '4000.00',
  credit: '0.00',
  closing_balance: '4000.00',
  prior_closing_balance: '0.00',
  is_adjustment: true,
};

// Two rows sharing account_code|source ("5-2000|journal") -- a split adjustment
// posted as two lines against the same account from the same source. Only the
// grouped comparator (Finding 1's fix) can tell these apart; a Map keyed by
// account_code|source would keep only the last one and silently drop the first.
const ROW_SPLIT_A = {
  account_code: '5-2000',
  account_name: 'Sundry Adjustments',
  source: 'journal',
  opening_balance: '0.00',
  debit: '100.00',
  credit: '0.00',
  closing_balance: '100.00',
  prior_closing_balance: '0.00',
  is_adjustment: true,
};
const ROW_SPLIT_B = {
  account_code: '5-2000',
  account_name: 'Sundry Adjustments',
  source: 'journal',
  opening_balance: '0.00',
  debit: '50.00',
  credit: '0.00',
  closing_balance: '50.00',
  prior_closing_balance: '0.00',
  is_adjustment: true,
};

const ASSET_LAPTOP = {
  asset_name: 'Laptop',
  opening_wdv: '2000.00',
  depreciation_amount: '400.00',
  private_depreciation: '0.00',
  closing_wdv: '1600.00',
  dep_expense_code: '6-1200',
  accum_dep_code: '1-2100',
};

// Two assets sharing asset_name ("Laptop") -- core/e2e_figures.py orders
// depreciation by (asset_name, pk) specifically because this happens (e.g. two
// laptops bought the same year), so only the grouped comparator can tell them apart.
const ASSET_LAPTOP_A = { ...ASSET_LAPTOP, closing_wdv: '1600.00' };
const ASSET_LAPTOP_B = { ...ASSET_LAPTOP, opening_wdv: '3000.00', closing_wdv: '2500.00' };

const BASELINE_FIXTURE = {
  after_depreciation_post: {
    trial_balance: [ROW_CASH, ROW_DEPRECIATION],
    totals: { debit: '4100.00', credit: '4100.00' },
    journals: [{ id: 'j1', description: 'depreciation' }],
    depreciation: [ASSET_LAPTOP],
  },
  after_split_adjustment: {
    trial_balance: [ROW_SPLIT_A, ROW_SPLIT_B],
    totals: { debit: '150.00', credit: '0.00' },
    journals: [],
  },
  after_two_assets: {
    trial_balance: [],
    totals: { debit: '0.00', credit: '0.00' },
    journals: [],
    depreciation: [ASSET_LAPTOP_A, ASSET_LAPTOP_B],
  },
};

function writeBaseline(fixture: any): void {
  fs.writeFileSync(BASELINE_PATH, JSON.stringify(fixture, null, 2) + '\n');
}

function clone(checkpoint: keyof typeof BASELINE_FIXTURE): any {
  return JSON.parse(JSON.stringify(BASELINE_FIXTURE[checkpoint]));
}

test.beforeEach(() => {
  writeBaseline(BASELINE_FIXTURE);
});

test('an exact match reports no differences', () => {
  const figures = clone('after_depreciation_post');
  expect(compareToBaseline('after_depreciation_post', figures, BASELINE_PATH)).toEqual([]);
});

test('a changed figure names the account, the field, and both values', () => {
  const figures = clone('after_depreciation_post');
  figures.trial_balance[1] = { ...figures.trial_balance[1], debit: '8000.00', closing_balance: '8000.00' };

  const diffs = compareToBaseline('after_depreciation_post', figures, BASELINE_PATH);

  expect(diffs).toContain(
    'after_depreciation_post: 6-1200|journal debit expected 4000.00, got 8000.00',
  );
  expect(diffs).toContain(
    'after_depreciation_post: 6-1200|journal closing_balance expected 4000.00, got 8000.00',
  );
});

test('a row missing from the run is reported against its expected closing balance', () => {
  const figures = clone('after_depreciation_post');
  figures.trial_balance = figures.trial_balance.filter((r: any) => r.account_code !== '6-1200');

  const diffs = compareToBaseline('after_depreciation_post', figures, BASELINE_PATH);

  expect(diffs).toContain(
    'after_depreciation_post: 6-1200|journal missing — expected closing 4000.00',
  );
});

test('a row absent from the baseline is reported as new', () => {
  const figures = clone('after_depreciation_post');
  figures.trial_balance.push({ ...ROW_CASH, account_code: '9-9999', source: 'manual' });

  const diffs = compareToBaseline('after_depreciation_post', figures, BASELINE_PATH);

  expect(diffs).toContain('after_depreciation_post: 9-9999|manual is new — not in the baseline');
});

test('totals and journal-count mismatches are reported', () => {
  const figures = clone('after_depreciation_post');
  figures.totals.debit = '9999.00';
  figures.journals = [];

  const diffs = compareToBaseline('after_depreciation_post', figures, BASELINE_PATH);

  expect(diffs).toContain(
    'after_depreciation_post: totals.debit expected 4100.00, got 9999.00',
  );
  expect(diffs.some((d) => d.includes('journals differ'))).toBe(true);
});

test('a changed prior_closing_balance is reported (Finding 4)', () => {
  // Load-bearing for the comparatives story -- see figures.ts's field list comment.
  // Undetected, a comparative-year regression would sail through unnoticed because
  // every other TB field on the row can be unchanged.
  const figures = clone('after_depreciation_post');
  figures.trial_balance[0] = { ...figures.trial_balance[0], prior_closing_balance: '999.00' };

  const diffs = compareToBaseline('after_depreciation_post', figures, BASELINE_PATH);

  expect(diffs).toContain(
    'after_depreciation_post: 1-1000|import prior_closing_balance expected 0.00, got 999.00',
  );
});

test('a changed account_name is reported (Finding 4)', () => {
  const figures = clone('after_depreciation_post');
  figures.trial_balance[0] = { ...figures.trial_balance[0], account_name: 'Petty Cash' };

  const diffs = compareToBaseline('after_depreciation_post', figures, BASELINE_PATH);

  expect(diffs).toContain(
    'after_depreciation_post: 1-1000|import account_name expected Cash, got Petty Cash',
  );
});

test('a changed is_adjustment is reported (Finding 4)', () => {
  const figures = clone('after_depreciation_post');
  figures.trial_balance[1] = { ...figures.trial_balance[1], is_adjustment: false };

  const diffs = compareToBaseline('after_depreciation_post', figures, BASELINE_PATH);

  expect(diffs).toContain(
    'after_depreciation_post: 6-1200|journal is_adjustment expected true, got false',
  );
});

test('a changed depreciation figure names the asset, the field, and both values (Finding 4)', () => {
  const figures = clone('after_depreciation_post');
  figures.depreciation[0] = { ...figures.depreciation[0], closing_wdv: '1200.00' };

  const diffs = compareToBaseline('after_depreciation_post', figures, BASELINE_PATH);

  expect(diffs).toContain(
    'after_depreciation_post: depreciation Laptop closing_wdv expected 1600.00, got 1200.00',
  );
});

test('a depreciation row missing from the run is reported against its expected closing_wdv (Finding 4)', () => {
  const figures = clone('after_depreciation_post');
  figures.depreciation = [];

  const diffs = compareToBaseline('after_depreciation_post', figures, BASELINE_PATH);

  expect(diffs).toContain(
    'after_depreciation_post: depreciation Laptop missing — expected closing_wdv 1600.00',
  );
});

test('a depreciation row absent from the baseline is reported as new (Finding 4)', () => {
  const figures = clone('after_depreciation_post');
  figures.depreciation.push({ ...ASSET_LAPTOP, asset_name: 'Delivery Van' });

  const diffs = compareToBaseline('after_depreciation_post', figures, BASELINE_PATH);

  expect(diffs).toContain(
    'after_depreciation_post: depreciation Delivery Van is new — not in the baseline',
  );
});

test('a regression on the second of two depreciation assets sharing a name is reported (Finding 4)', () => {
  const figures = clone('after_two_assets');
  figures.depreciation[1] = { ...figures.depreciation[1], closing_wdv: '0.00' };

  const diffs = compareToBaseline('after_two_assets', figures, BASELINE_PATH);

  expect(diffs).toContain(
    'after_two_assets: depreciation Laptop[1] closing_wdv expected 2500.00, got 0.00',
  );
  expect(diffs.some((d) => d.includes('Laptop[0]'))).toBe(false);
});

test('a checkpoint the baseline has never seen is reported, not silently passed', () => {
  const diffs = compareToBaseline(
    'never_seen_checkpoint',
    { trial_balance: [], totals: {} },
    BASELINE_PATH,
  );

  expect(diffs).toEqual([
    'never_seen_checkpoint: not in the baseline yet — review the observed figures and bless them',
  ]);
});

test('a missing baseline file throws rather than comparing against nothing', () => {
  const missingPath = path.join(SCRATCH_DIR, 'does-not-exist.json');
  expect(() => compareToBaseline('after_depreciation_post', {}, missingPath)).toThrow(
    /no golden baseline/,
  );
});

test('a regression on the second of two rows colliding on the same key is reported', () => {
  const figures = clone('after_split_adjustment');
  // Only the second row (index 1) regresses. A Map keyed by account_code|source alone
  // would have kept just one of the two baseline rows and one of the two actual rows
  // (whichever was inserted last), so this specific case -- the first row still
  // matching while the second silently doesn't -- is exactly what a non-grouping
  // comparator would fail to notice.
  figures.trial_balance[1] = { ...figures.trial_balance[1], debit: '999.00', closing_balance: '999.00' };

  const diffs = compareToBaseline('after_split_adjustment', figures, BASELINE_PATH);

  expect(diffs).toContain(
    'after_split_adjustment: 5-2000|journal[1] debit expected 50.00, got 999.00',
  );
  expect(diffs).toContain(
    'after_split_adjustment: 5-2000|journal[1] closing_balance expected 50.00, got 999.00',
  );
  // The untouched first row must not be swept up in the same report.
  expect(diffs.some((d) => d.includes('5-2000|journal[0]'))).toBe(false);
});

test('a colliding key with a different number of rows on each side reports the count mismatch', () => {
  const figures = clone('after_split_adjustment');
  figures.trial_balance = [figures.trial_balance[0]]; // drop the second 5-2000|journal row

  const diffs = compareToBaseline('after_split_adjustment', figures, BASELINE_PATH);

  expect(diffs).toContain(
    'after_split_adjustment: 5-2000|journal[1] missing — expected closing 50.00',
  );
});
