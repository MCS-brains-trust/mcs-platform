import { execFile } from 'child_process';
import { promisify } from 'util';
import * as fs from 'fs';
import * as path from 'path';

const execFileAsync = promisify(execFile);

import { REPO_DIR, VENV_PYTHON } from './paths';
const BASELINE = path.join(__dirname, '..', 'tier2', 'figures.baseline.json');
// One file per checkpoint rather than one shared JSON document. playwright.config.ts
// runs spec *files* concurrently across workers (fullyParallel: false only serialises
// tests within a file), and Tasks 8/9 add the second and third tier2 spec file — so a
// single read-modify-write file would let two workers finishing near each other lose
// one worker's checkpoint. bless_figures.sh reads every file in this directory.
const OBSERVED_DIR = path.join(__dirname, '..', 'test-results', 'observed-figures');
const MAX_REPORTED_DIFFS = 20;

/** Run e2e_dump_figures against a specific branch and parse its JSON. */
export async function dumpFigures(
  dbName: string,
  yearId: string,
  checkpoint: string,
): Promise<any> {
  const { stdout } = await execFileAsync(
    VENV_PYTHON,
    ['manage.py', 'e2e_dump_figures', '--year', yearId, '--checkpoint', checkpoint],
    {
      cwd: REPO_DIR,
      env: {
        ...process.env,
        DJANGO_SETTINGS_MODULE: 'config.settings_e2e',
        E2E_DB_NAME: dbName,
      },
      maxBuffer: 32 * 1024 * 1024,
    },
  );
  return JSON.parse(stdout).figures;
}

/**
 * Record an observed snapshot for bless_figures.sh to promote.
 *
 * Writes its own file rather than merging into a shared one -- see OBSERVED_DIR's
 * comment. Each checkpoint is a unique label by construction (the baseline itself is
 * keyed by checkpoint, so two spec files racing to define the same one would already
 * be a modelling bug, not something a lock could paper over), so filenames derived
 * from the checkpoint text cannot collide across a healthy run.
 */
export function recordObserved(checkpoint: string, figures: any): void {
  fs.mkdirSync(OBSERVED_DIR, { recursive: true });
  const safeName = checkpoint.replace(/[^a-zA-Z0-9_.-]/g, '_');
  const file = path.join(OBSERVED_DIR, `${safeName}.json`);
  fs.writeFileSync(file, JSON.stringify({ checkpoint, figures }, null, 2) + '\n');
}

/**
 * Group rows by a caller-supplied key, preserving row order within a key.
 *
 * Both trial-balance rows and depreciation rows can legitimately repeat their
 * "obvious" identifier (core/e2e_figures.py orders each by a tie-breaking pk for
 * exactly this reason: two lines can share account_code|source, and two assets can
 * share asset_name), so grouping instead of overwriting is needed in both places —
 * see keyFn below for the two shapes this is called with.
 */
function groupRows(rows: any[] | undefined, keyFn: (row: any) => string): Map<string, any[]> {
  const groups = new Map<string, any[]>();
  for (const r of rows ?? []) {
    const key = keyFn(r);
    const group = groups.get(key);
    if (group) group.push(r);
    else groups.set(key, [r]);
  }
  return groups;
}

const TB_KEY = (r: any) => `${r.account_code}|${r.source}`;
const DEPRECIATION_KEY = (r: any) => r.asset_name;

/** Fields compared on every trial-balance row. Keep in sync with what
 * core/e2e_figures.py emits and bless_figures.sh's describe_changes prints —
 * a field compared here but not printed there is exactly Finding 5's bug. */
const TB_FIELDS = [
  'opening_balance',
  'debit',
  'credit',
  'closing_balance',
  'prior_closing_balance',
  'account_name',
  'is_adjustment',
];

/** Fields compared on every depreciation row. */
const DEPRECIATION_FIELDS = [
  'opening_wdv',
  'depreciation_amount',
  'private_depreciation',
  'closing_wdv',
  'dep_expense_code',
  'accum_dep_code',
];

/**
 * Compare a snapshot to the blessed baseline.
 *
 * Returns one line per difference rather than a whole-document JSON diff, because a
 * dump is hundreds of rows and the useful information is which account moved. A
 * stacking regression should read as
 *   after_depreciation_post: 6-1200 debit expected 4000.00, got 8000.00
 * and name itself in the failure line.
 *
 * `baselinePath` defaults to the committed golden file; the parameter exists so a
 * test can point this at a throwaway fixture instead of the real
 * tier2/figures.baseline.json, without changing any production call site.
 */
export function compareToBaseline(
  checkpoint: string,
  figures: any,
  baselinePath: string = BASELINE,
): string[] {
  if (!fs.existsSync(baselinePath)) {
    throw new Error(
      `no golden baseline at ${baselinePath}. Run the specs, review the output, then bless ` +
        `with: bash scripts/bless_figures.sh`,
    );
  }
  const baseline = JSON.parse(fs.readFileSync(baselinePath, 'utf-8'));
  const expected = baseline[checkpoint];
  if (!expected) {
    return [
      `${checkpoint}: not in the baseline yet — review the observed figures and bless them`,
    ];
  }

  const diffs: string[] = [];

  // core/e2e_figures.py deliberately allows more than one TrialBalanceLine to share
  // an account_code (that is why the dump itself tie-breaks on pk for a total order),
  // so account_code|source alone is not always a unique key here. Grouping instead of
  // overwriting means a regression on the second row of a colliding pair still gets a
  // diff line, rather than being silently shadowed by the first row's match.
  const expectedGroups = groupRows(expected.trial_balance, TB_KEY);
  const actualGroups = groupRows(figures.trial_balance, TB_KEY);
  const allKeys = new Set<string>([...expectedGroups.keys(), ...actualGroups.keys()]);

  for (const key of allKeys) {
    const expGroup = expectedGroups.get(key) ?? [];
    const actGroup = actualGroups.get(key) ?? [];
    const maxLen = Math.max(expGroup.length, actGroup.length);
    // Rows within a colliding key are compared pairwise by position. An index suffix
    // is added only when a key actually holds more than one row on either side, so
    // the overwhelmingly common one-row-per-key case keeps its plain, existing label.
    for (let i = 0; i < maxLen; i++) {
      const label = maxLen > 1 ? `${key}[${i}]` : key;
      const exp = expGroup[i];
      const act = actGroup[i];
      if (exp && !act) {
        diffs.push(`${checkpoint}: ${label} missing — expected closing ${exp.closing_balance}`);
        continue;
      }
      if (act && !exp) {
        diffs.push(`${checkpoint}: ${label} is new — not in the baseline`);
        continue;
      }
      for (const field of TB_FIELDS) {
        if (exp[field] !== act[field]) {
          diffs.push(
            `${checkpoint}: ${label} ${field} expected ${exp[field]}, got ${act[field]}`,
          );
        }
      }
    }
  }

  // Same grouped, pairwise-by-position comparison as the trial balance above, and for
  // the same reason: core/e2e_figures.py orders depreciation by (asset_name, pk)
  // specifically because two assets can share a name (e.g. two "Laptop" purchases in
  // the same year), so asset_name alone is not a unique key here either.
  const expectedDepGroups = groupRows(expected.depreciation, DEPRECIATION_KEY);
  const actualDepGroups = groupRows(figures.depreciation, DEPRECIATION_KEY);
  const allDepKeys = new Set<string>([...expectedDepGroups.keys(), ...actualDepGroups.keys()]);

  for (const key of allDepKeys) {
    const expGroup = expectedDepGroups.get(key) ?? [];
    const actGroup = actualDepGroups.get(key) ?? [];
    const maxLen = Math.max(expGroup.length, actGroup.length);
    for (let i = 0; i < maxLen; i++) {
      const label = maxLen > 1 ? `${key}[${i}]` : key;
      const exp = expGroup[i];
      const act = actGroup[i];
      if (exp && !act) {
        diffs.push(`${checkpoint}: depreciation ${label} missing — expected closing_wdv ${exp.closing_wdv}`);
        continue;
      }
      if (act && !exp) {
        diffs.push(`${checkpoint}: depreciation ${label} is new — not in the baseline`);
        continue;
      }
      for (const field of DEPRECIATION_FIELDS) {
        if (exp[field] !== act[field]) {
          diffs.push(
            `${checkpoint}: depreciation ${label} ${field} expected ${exp[field]}, got ${act[field]}`,
          );
        }
      }
    }
  }

  for (const field of ['debit', 'credit']) {
    if (expected.totals?.[field] !== figures.totals?.[field]) {
      diffs.push(
        `${checkpoint}: totals.${field} expected ${expected.totals?.[field]}, ` +
          `got ${figures.totals?.[field]}`,
      );
    }
  }

  const expectedJournals = JSON.stringify(expected.journals ?? []);
  const actualJournals = JSON.stringify(figures.journals ?? []);
  if (expectedJournals !== actualJournals) {
    diffs.push(
      `${checkpoint}: journals differ — expected ${(expected.journals ?? []).length} ` +
        `journal(s), got ${(figures.journals ?? []).length}`,
    );
  }

  return diffs.slice(0, MAX_REPORTED_DIFFS);
}
