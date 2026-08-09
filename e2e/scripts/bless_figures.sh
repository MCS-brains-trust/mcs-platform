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
OBSERVED="${E2E_DIR}/test-results/observed-figures.json"
BASELINE="${E2E_DIR}/tier2/figures.baseline.json"

if [[ ! -f "${OBSERVED}" ]]; then
    echo "no observed figures at ${OBSERVED} — run the tier2 suite first" >&2
    exit 1
fi

python3 - "$OBSERVED" "$BASELINE" <<'PY'
import json
import sys
from pathlib import Path

observed_path, baseline_path = Path(sys.argv[1]), Path(sys.argv[2])
observed = json.loads(observed_path.read_text())
old = json.loads(baseline_path.read_text()) if baseline_path.exists() else {}

for checkpoint, figures in sorted(observed.items()):
    if checkpoint not in old:
        print(f"\n+ {checkpoint}: newly baselined "
              f"({len(figures.get('trial_balance', []))} TB rows)")
        continue
    before = old[checkpoint]
    before_rows = {r["account_code"]: r for r in before.get("trial_balance", [])}
    after_rows = {r["account_code"]: r for r in figures.get("trial_balance", [])}
    changed = [
        f"  ~ {code} closing {before_rows[code]['closing_balance']} -> "
        f"{after_rows[code]['closing_balance']}"
        for code in sorted(before_rows.keys() & after_rows.keys())
        if before_rows[code]["closing_balance"] != after_rows[code]["closing_balance"]
    ]
    if changed:
        print(f"\n~ {checkpoint}: {len(changed)} account(s) changed — confirm each is intended:")
        print("\n".join(changed))

# Checkpoints not seen in this run are kept: a filtered run is no evidence that they
# changed, and dropping them would silently delete coverage.
merged = {**old, **observed}
baseline_path.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n")
print(f"\nbaseline written: {len(merged)} checkpoint(s) → {baseline_path}")
PY
