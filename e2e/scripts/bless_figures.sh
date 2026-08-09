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

E2E_DIR="${STATEMENTHUB_ROOT:-/opt/statementhub}/e2e"
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


# Kept in sync with figures.ts's TB_FIELDS/DEPRECIATION_FIELDS by hand — this
# script and that comparator must agree on what "changed" means, because printing
# fewer fields than the comparator checks is exactly Finding 5's bug: a change the
# comparator would fail on gets promoted here with nothing printed, so the operator
# sees "no changes" and commits a baseline that hides a real regression.
TB_FIELDS = [
    "opening_balance", "debit", "credit", "closing_balance",
    "prior_closing_balance", "account_name", "is_adjustment",
]
DEPRECIATION_FIELDS = [
    "opening_wdv", "depreciation_amount", "private_depreciation",
    "closing_wdv", "dep_expense_code", "accum_dep_code",
]


def group_rows(rows, key_fn):
    """Group rows by a caller-supplied key, same as figures.ts's compareToBaseline
    — both trial-balance rows (account_code|source) and depreciation rows
    (asset_name) can legitimately repeat their identifier (see core/e2e_figures.py),
    so the print here has to agree with the comparator on what counts as "the same
    row", or an added/removed/changed row at that granularity would print
    differently here than it fails there."""
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(key_fn(r), []).append(r)
    return groups


def describe_group_changes(before_rows, after_rows, key_fn, fields, id_field, label_prefix=""):
    """Return (changed, added, removed) description lines for one group of rows
    (a checkpoint's trial balance, or its depreciation schedule), matching
    compareToBaseline's pairwise-by-position comparison within a colliding key so
    this print and that assertion never disagree. Prints every field in `fields`,
    not just one, so nothing the comparator checks can change silently."""
    before_groups = group_rows(before_rows, key_fn)
    after_groups = group_rows(after_rows, key_fn)
    changed, added, removed = [], [], []

    for key in sorted(set(before_groups) | set(after_groups)):
        before_grp = before_groups.get(key, [])
        after_grp = after_groups.get(key, [])
        max_len = max(len(before_grp), len(after_grp))
        for i in range(max_len):
            label = f"{label_prefix}{key}[{i}]" if max_len > 1 else f"{label_prefix}{key}"
            before_row = before_grp[i] if i < len(before_grp) else None
            after_row = after_grp[i] if i < len(after_grp) else None
            if before_row is None:
                added.append(f"  + {label} {id_field} {after_row[id_field]}")
            elif after_row is None:
                removed.append(f"  - {label} {id_field} {before_row[id_field]}")
            else:
                for field in fields:
                    if before_row.get(field) != after_row.get(field):
                        changed.append(
                            f"  ~ {label} {field}: {before_row.get(field)} -> "
                            f"{after_row.get(field)}"
                        )
    return changed, added, removed


def describe_totals_and_journals(before, after):
    """Return description lines for totals and journal changes -- the two fields
    compareToBaseline fails on that are not per-row, and which describe_changes
    previously merged silently with everything else it didn't print either."""
    lines = []
    before_totals = before.get("totals", {})
    after_totals = after.get("totals", {})
    for field in ("debit", "credit"):
        if before_totals.get(field) != after_totals.get(field):
            lines.append(
                f"  ~ totals.{field}: {before_totals.get(field)} -> {after_totals.get(field)}"
            )
    before_journals = before.get("journals", [])
    after_journals = after.get("journals", [])
    if json.dumps(before_journals, sort_keys=True) != json.dumps(after_journals, sort_keys=True):
        lines.append(
            f"  ~ journals: {len(before_journals)} journal(s) -> {len(after_journals)} journal(s) "
            "(content differs — see the observed-figures file for detail)"
        )
    return lines


for checkpoint, figures in sorted(observed.items()):
    if checkpoint not in old:
        print(f"\n+ {checkpoint}: newly baselined "
              f"({len(figures.get('trial_balance', []))} TB rows, "
              f"{len(figures.get('depreciation', []))} depreciation row(s))")
        continue
    before = old[checkpoint]

    tb_changed, tb_added, tb_removed = describe_group_changes(
        before.get("trial_balance", []), figures.get("trial_balance", []),
        key_fn=lambda r: f"{r['account_code']}|{r['source']}",
        fields=TB_FIELDS, id_field="closing_balance",
    )
    dep_changed, dep_added, dep_removed = describe_group_changes(
        before.get("depreciation", []), figures.get("depreciation", []),
        key_fn=lambda r: r["asset_name"],
        fields=DEPRECIATION_FIELDS, id_field="closing_wdv", label_prefix="depreciation ",
    )
    totals_journals = describe_totals_and_journals(before, figures)

    lines = tb_changed + tb_added + tb_removed + dep_changed + dep_added + dep_removed + totals_journals
    if lines:
        print(f"\n~ {checkpoint}: {len(lines)} change(s) — confirm each is intended:")
        for line in lines:
            print(line)

# Checkpoints not seen in this run are kept: a filtered run is no evidence that they
# changed, and dropping them would silently delete coverage.
merged = {**old, **observed}
baseline_path.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n")
print(f"\nbaseline written: {len(merged)} checkpoint(s) → {baseline_path}")
PY
