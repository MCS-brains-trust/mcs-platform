#!/usr/bin/env bash
#
# Promote the statuses observed in the last run to the committed baseline.
#
#   npm run bless
#
# Blessing is deliberately a separate, explicit step rather than something the suite
# does for itself. If the suite rewrote its own expectations on every run it could
# never detect a regression — a route that broke would simply be recorded as broken
# and reported as passing from then on.
#
# So: read the diff this prints, satisfy yourself that every change is intended, and
# commit the result alongside the change that caused it.

set -euo pipefail

E2E_DIR="/opt/statementhub/e2e"
OBSERVED="${E2E_DIR}/test-results/observed-status.json"
BASELINE="${E2E_DIR}/tier1/status.baseline.json"

if [[ ! -f "${OBSERVED}" ]]; then
    echo "no observed statuses at ${OBSERVED} — run the tier1 suite first" >&2
    exit 1
fi

python3 - "$OBSERVED" "$BASELINE" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

observed_path, baseline_path = Path(sys.argv[1]), Path(sys.argv[2])
raw = json.loads(observed_path.read_text())

# A 5xx is never a valid expectation. Recording one would turn a live server error
# into the documented behaviour of the route, and since the suite fails on 5xx
# regardless of the baseline, a blessed 500 would be a permanently red test that
# looks sanctioned. Left out entirely, so the failure keeps pointing at the bug.
server_errors = {k: v for k, v in raw.items() if v >= 500}
observed = {k: v for k, v in raw.items() if v < 500}

if server_errors:
    print(f"\nREFUSED to baseline {len(server_errors)} route(s) returning 5xx — fix these:")
    for k, v in sorted(server_errors.items()):
        print(f"  ! {k}: {v}")

old = {}
if baseline_path.exists():
    old = json.loads(baseline_path.read_text()).get("statuses", {})

added = {k: v for k, v in observed.items() if k not in old}
changed = {k: (old[k], v) for k, v in observed.items() if k in old and old[k] != v}
missing = {k: v for k, v in old.items() if k not in observed}

if added:
    print(f"\n{len(added)} newly baselined route(s):")
    for k, v in sorted(added.items()):
        print(f"  + {k}: {v}")

if changed:
    print(f"\n{len(changed)} route(s) CHANGED status — confirm each is intended:")
    for k, (before, after) in sorted(changed.items()):
        print(f"  ~ {k}: {before} -> {after}")

if missing:
    print(f"\n{len(missing)} baselined route(s) were not observed in the last run")
    print("  (removed from the manifest, or the run was filtered with -g)")
    for k in sorted(missing):
        print(f"  ? {k}")

# Baselined-but-unobserved entries are kept. Dropping them would mean a filtered run
# silently deleted coverage for every route it did not touch.
merged = {**old, **observed}

# 5xx routes are recorded here rather than in statuses. They keep failing the sweep,
# which is the point, but listing them means the completeness check can tell a route
# that is broken from a route nobody has looked at yet.
old_known = set(json.loads(baseline_path.read_text()).get("known_failures", [])
                if baseline_path.exists() else [])

# A previously-known failure that was observed returning under 500 has been fixed, so it
# is dropped. Entries not seen in this run are kept, because a filtered run is no
# evidence that they are fixed.
fixed = {k for k in old_known if k in observed}
known = sorted((old_known - fixed) | set(server_errors))

if fixed:
    print(f"\n{len(fixed)} previously-broken route(s) now return a non-5xx status:")
    for k in sorted(fixed):
        print(f"  FIXED {k}: {observed[k]}")

baseline_path.write_text(json.dumps({
    "_comment": (
        "Expected HTTP status per route for the Tier 1 sweep. Any change is a test "
        "failure. Regenerate deliberately with npm run bless, never automatically, and "
        "commit alongside the change that caused it."
    ),
    "_known_failures_comment": (
        "Routes currently returning 5xx. These are NOT baselined and still fail the "
        "sweep; they are listed only so a broken route is distinguishable from an "
        "unreviewed one. Remove an entry when the underlying bug is fixed."
    ),
    "blessed_at": datetime.now(timezone.utc).isoformat(),
    "known_failures": known,
    "statuses": dict(sorted(merged.items())),
}, indent=2) + "\n")

if known:
    print(f"{len(known)} route(s) recorded as known 5xx failures")

print(f"\nbaseline written: {len(merged)} routes → {baseline_path}")
if not (added or changed or missing):
    print("(no changes)")
PY
