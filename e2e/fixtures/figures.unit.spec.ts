import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';
import { compareToBaseline } from './figures';

/**
 * Pure-logic coverage for compareToBaseline.
 *
 * The comparator is the only thing standing between a real regression and a green
 * run — everything else in this file just drives it. A comparator that silently
 * returned [] when a figure moved would make every Tier 2 assertion vacuous, so this
 * checks the four shapes a diff has to notice: a changed figure, a row the baseline
 * expects but the run didn't produce, a row the run produced that the baseline
 * doesn't know about, and a checkpoint the baseline has never seen at all.
 *
 * compareToBaseline reads its baseline from a fixed path (tier2/figures.baseline.json)
 * rather than an injectable one, so this test writes the real file there for the
 * duration of the run and restores whatever was there before -- there is no other way
 * to exercise the exported function as written.
 */

const BASELINE_PATH = path.join(__dirname, '..', 'tier2', 'figures.baseline.json');
const PRE_EXISTING = fs.existsSync(BASELINE_PATH)
  ? fs.readFileSync(BASELINE_PATH, 'utf-8')
  : null;

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

const BASELINE_FIXTURE = {
  after_depreciation_post: {
    trial_balance: [ROW_CASH, ROW_DEPRECIATION],
    totals: { debit: '4100.00', credit: '4100.00' },
    journals: [{ id: 'j1', description: 'depreciation' }],
  },
};

test.beforeAll(() => {
  fs.mkdirSync(path.dirname(BASELINE_PATH), { recursive: true });
  fs.writeFileSync(BASELINE_PATH, JSON.stringify(BASELINE_FIXTURE, null, 2) + '\n');
});

test.afterAll(() => {
  if (PRE_EXISTING === null) {
    fs.rmSync(BASELINE_PATH, { force: true });
  } else {
    fs.writeFileSync(BASELINE_PATH, PRE_EXISTING);
  }
});

test('an exact match reports no differences', () => {
  const figures = JSON.parse(JSON.stringify(BASELINE_FIXTURE.after_depreciation_post));
  expect(compareToBaseline('after_depreciation_post', figures)).toEqual([]);
});

test('a changed figure names the account, the field, and both values', () => {
  const figures = JSON.parse(JSON.stringify(BASELINE_FIXTURE.after_depreciation_post));
  figures.trial_balance[1] = { ...figures.trial_balance[1], debit: '8000.00', closing_balance: '8000.00' };

  const diffs = compareToBaseline('after_depreciation_post', figures);

  expect(diffs).toContain(
    'after_depreciation_post: 6-1200|journal debit expected 4000.00, got 8000.00',
  );
  expect(diffs).toContain(
    'after_depreciation_post: 6-1200|journal closing_balance expected 4000.00, got 8000.00',
  );
});

test('a row missing from the run is reported against its expected closing balance', () => {
  const figures = JSON.parse(JSON.stringify(BASELINE_FIXTURE.after_depreciation_post));
  figures.trial_balance = figures.trial_balance.filter((r: any) => r.account_code !== '6-1200');

  const diffs = compareToBaseline('after_depreciation_post', figures);

  expect(diffs).toContain(
    'after_depreciation_post: 6-1200|journal missing — expected closing 4000.00',
  );
});

test('a row absent from the baseline is reported as new', () => {
  const figures = JSON.parse(JSON.stringify(BASELINE_FIXTURE.after_depreciation_post));
  figures.trial_balance.push({ ...ROW_CASH, account_code: '9-9999', source: 'manual' });

  const diffs = compareToBaseline('after_depreciation_post', figures);

  expect(diffs).toContain('after_depreciation_post: 9-9999|manual is new — not in the baseline');
});

test('totals and journal-count mismatches are reported', () => {
  const figures = JSON.parse(JSON.stringify(BASELINE_FIXTURE.after_depreciation_post));
  figures.totals.debit = '9999.00';
  figures.journals = [];

  const diffs = compareToBaseline('after_depreciation_post', figures);

  expect(diffs).toContain(
    'after_depreciation_post: totals.debit expected 4100.00, got 9999.00',
  );
  expect(diffs.some((d) => d.includes('journals differ'))).toBe(true);
});

test('a checkpoint the baseline has never seen is reported, not silently passed', () => {
  const diffs = compareToBaseline('never_seen_checkpoint', { trial_balance: [], totals: {} });

  expect(diffs).toEqual([
    'never_seen_checkpoint: not in the baseline yet — review the observed figures and bless them',
  ]);
});

test('a missing baseline file throws rather than comparing against nothing', () => {
  fs.rmSync(BASELINE_PATH, { force: true });
  try {
    expect(() => compareToBaseline('after_depreciation_post', {})).toThrow(/no golden baseline/);
  } finally {
    // Restore for any remaining tests in this file / worker.
    fs.writeFileSync(BASELINE_PATH, JSON.stringify(BASELINE_FIXTURE, null, 2) + '\n');
  }
});
