import { execFile } from 'child_process';
import { promisify } from 'util';
import * as fs from 'fs';
import * as path from 'path';

const execFileAsync = promisify(execFile);

const REPO_DIR = '/opt/statementhub';
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

/** Group trial-balance rows by account_code|source, preserving row order within a key. */
function groupRows(rows: any[] | undefined): Map<string, any[]> {
  const groups = new Map<string, any[]>();
  for (const r of rows ?? []) {
    const key = `${r.account_code}|${r.source}`;
    const group = groups.get(key);
    if (group) group.push(r);
    else groups.set(key, [r]);
  }
  return groups;
}

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
  const expectedGroups = groupRows(expected.trial_balance);
  const actualGroups = groupRows(figures.trial_balance);
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
      for (const field of ['opening_balance', 'debit', 'credit', 'closing_balance']) {
        if (exp[field] !== act[field]) {
          diffs.push(
            `${checkpoint}: ${label} ${field} expected ${exp[field]}, got ${act[field]}`,
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
