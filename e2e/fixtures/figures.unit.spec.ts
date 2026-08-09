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

const BASELINE_FIXTURE = {
  after_depreciation_post: {
    trial_balance: [ROW_CASH, ROW_DEPRECIATION],
    totals: { debit: '4100.00', credit: '4100.00' },
    journals: [{ id: 'j1', description: 'depreciation' }],
  },
  after_split_adjustment: {
    trial_balance: [ROW_SPLIT_A, ROW_SPLIT_B],
    totals: { debit: '150.00', credit: '0.00' },
    journals: [],
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
