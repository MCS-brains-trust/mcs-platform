#!/usr/bin/env bash
#
# Promote the figures observed in the last Tier 2 run to the committed baseline.
#
#   npm run bless:figures
#
# Manual for the same reason as bless_baseline.sh: a suite that rewrote its own
# expectations could never detect a regression. Read the diff, satisfy yourself that
# every changed figure is intended, and commit it alongside the change that caused it.
#
# Note the division of labour — the rule-based assertions in the specs (a TB that
# balances, opening equal to prior closing, depreciation posting idempotently) are NOT
# blessable. They either hold or the run fails. This file only records figures whose
# correctness a human has confirmed.

set -euo pipefail

E2E_DIR="/opt/statementhub/e2e"
# One file per checkpoint, not one shared JSON document — see figures.ts's
# recordObserved for why (concurrent spec files would otherwise race on a single
# read-modify-write file and silently lose a checkpoint).
OBSERVED_DIR="${E2E_DIR}/test-results/observed-figures"
BASELINE="${E2E_DIR}/tier2/figures.baseline.json"

if [[ ! -d "${OBSERVED_DIR}" ]] || [[ -z "$(ls -A "${OBSERVED_DIR}" 2>/dev/null)" ]]; then
    echo "no observed figures in ${OBSERVED_DIR} — run the tier2 suite first" >&2
    exit 1
fi

python3 - "$OBSERVED_DIR" "$BASELINE" <<'PY'
import json
import sys
from pathlib import Path

observed_dir, baseline_path = Path(sys.argv[1]), Path(sys.argv[2])

# Each file holds exactly one checkpoint's {"checkpoint": ..., "figures": ...}, written
# by figures.ts's recordObserved. Keyed here by the file's own "checkpoint" field
# rather than its filename, so the merge is correct even if a checkpoint's name needed
# sanitising to become a safe filename.
observed = {}
for path in sorted(observed_dir.glob("*.json")):
    payload = json.loads(path.read_text())
    observed[payload["checkpoint"]] = payload["figures"]

old = json.loads(baseline_path.read_text()) if baseline_path.exists() else {}


def group_rows(rows):
    """Group trial-balance rows by account_code|source, same as figures.ts's
    compareToBaseline — an account_code can legitimately repeat (see
    core/e2e_figures.py), so the print here has to agree with the comparator on what
    counts as "the same row", or an added/removed/changed row at that granularity
    would print differently here than it fails there."""
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(f"{r['account_code']}|{r['source']}", []).append(r)
    return groups


def describe_changes(before_tb, after_tb):
    """Return (changed, added, removed) description lines for one checkpoint's
    trial balance, matching compareToBaseline's pairwise-by-position comparison
    within a colliding key so this print and that assertion never disagree."""
    before_groups = group_rows(before_tb)
    after_groups = group_rows(after_tb)
    changed, added, removed = [], [], []

    for key in sorted(set(before_groups) | set(after_groups)):
        before_rows = before_groups.get(key, [])
        after_rows = after_groups.get(key, [])
        max_len = max(len(before_rows), len(after_rows))
        for i in range(max_len):
            label = f"{key}[{i}]" if max_len > 1 else key
            before_row = before_rows[i] if i < len(before_rows) else None
            after_row = after_rows[i] if i < len(after_rows) else None
            if before_row is None:
                added.append(f"  + {label} closing {after_row['closing_balance']}")
            elif after_row is None:
                removed.append(f"  - {label} closing {before_row['closing_balance']}")
            elif before_row["closing_balance"] != after_row["closing_balance"]:
                changed.append(
                    f"  ~ {label} closing {before_row['closing_balance']} -> "
                    f"{after_row['closing_balance']}"
                )
    return changed, added, removed


for checkpoint, figures in sorted(observed.items()):
    if checkpoint not in old:
        print(f"\n+ {checkpoint}: newly baselined "
              f"({len(figures.get('trial_balance', []))} TB rows)")
        continue
    before = old[checkpoint]
    changed, added, removed = describe_changes(
        before.get("trial_balance", []), figures.get("trial_balance", [])
    )
    total = len(changed) + len(added) + len(removed)
    if total:
        print(f"\n~ {checkpoint}: {total} trial-balance row(s) changed — confirm each is intended:")
        for line in changed + added + removed:
            print(line)

# Checkpoints not seen in this run are kept: a filtered run is no evidence that they
# changed, and dropping them would silently delete coverage.
merged = {**old, **observed}
baseline_path.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n")
print(f"\nbaseline written: {len(merged)} checkpoint(s) → {baseline_path}")
PY
