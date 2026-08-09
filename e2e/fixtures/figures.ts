import { execFile } from 'child_process';
import { promisify } from 'util';
import * as fs from 'fs';
import * as path from 'path';

const execFileAsync = promisify(execFile);

const REPO_DIR = '/opt/statementhub';
const BASELINE = path.join(__dirname, '..', 'tier2', 'figures.baseline.json');
const OBSERVED = path.join(__dirname, '..', 'test-results', 'observed-figures.json');
const MAX_REPORTED_DIFFS = 20;

/** Run e2e_dump_figures against a specific branch and parse its JSON. */
export async function dumpFigures(
  dbName: string,
  yearId: string,
  checkpoint: string,
): Promise<any> {
  const { stdout } = await execFileAsync(
    `${REPO_DIR}/venv/bin/python`,
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

/** Append an observed snapshot for bless_figures.sh to promote. */
export function recordObserved(checkpoint: string, figures: any): void {
  fs.mkdirSync(path.dirname(OBSERVED), { recursive: true });
  const existing = fs.existsSync(OBSERVED)
    ? JSON.parse(fs.readFileSync(OBSERVED, 'utf-8'))
    : {};
  existing[checkpoint] = figures;
  fs.writeFileSync(OBSERVED, JSON.stringify(existing, null, 2) + '\n');
}

/**
 * Compare a snapshot to the blessed baseline.
 *
 * Returns one line per difference rather than a whole-document JSON diff, because a
 * dump is hundreds of rows and the useful information is which account moved. A
 * stacking regression should read as
 *   after_depreciation_post: 6-1200 debit expected 4000.00, got 8000.00
 * and name itself in the failure line.
 */
export function compareToBaseline(checkpoint: string, figures: any): string[] {
  if (!fs.existsSync(BASELINE)) {
    throw new Error(
      `no golden baseline at ${BASELINE}. Run the specs, review the output, then bless ` +
        `with: bash scripts/bless_figures.sh`,
    );
  }
  const baseline = JSON.parse(fs.readFileSync(BASELINE, 'utf-8'));
  const expected = baseline[checkpoint];
  if (!expected) {
    return [
      `${checkpoint}: not in the baseline yet — review the observed figures and bless them`,
    ];
  }

  const diffs: string[] = [];

  const expectedRows = new Map<string, any>(
    (expected.trial_balance ?? []).map((r: any) => [`${r.account_code}|${r.source}`, r]),
  );
  const actualRows = new Map<string, any>(
    (figures.trial_balance ?? []).map((r: any) => [`${r.account_code}|${r.source}`, r]),
  );

  for (const [key, exp] of expectedRows) {
    const act = actualRows.get(key);
    if (!act) {
      diffs.push(`${checkpoint}: ${key} missing — expected closing ${exp.closing_balance}`);
      continue;
    }
    for (const field of ['opening_balance', 'debit', 'credit', 'closing_balance']) {
      if (exp[field] !== act[field]) {
        diffs.push(
          `${checkpoint}: ${key} ${field} expected ${exp[field]}, got ${act[field]}`,
        );
      }
    }
  }
  for (const key of actualRows.keys()) {
    if (!expectedRows.has(key)) {
      diffs.push(`${checkpoint}: ${key} is new — not in the baseline`);
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
