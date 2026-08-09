#!/usr/bin/env bash
#
# Fail if the set of failing Tier 2 tests differs from tier2/known_failures.json.
#
#   npm run check:tier2-failures
#
# Two Tier 2 tests are intentionally red, each pinned to a confirmed application
# defect (see e2e/README.md's "Known defects found by the suite"). Nothing else
# distinguishes those two from a genuine new regression unless something checks —
# Tier 1 solves the equivalent problem for 5xx routes with its own known_failures
# list; this is the same idea for Tier 2's test titles.
#
# Implemented as a standalone script rather than a Playwright assertion so it can
# inspect the *completed* run's JSON reporter output (test-results/results.json,
# already configured in playwright.config.ts) instead of racing its own report — a
# test can only see its own outcome, never the whole suite's, while it is running.

set -euo pipefail

E2E_DIR="/opt/statementhub/e2e"
RESULTS="${E2E_DIR}/test-results/results.json"
KNOWN="${E2E_DIR}/tier2/known_failures.json"

if [[ ! -f "${RESULTS}" ]]; then
    echo "no results at ${RESULTS} — run the tier2 suite first (npm run test:tier2)" >&2
    exit 1
fi

python3 - "$RESULTS" "$KNOWN" <<'PY'
import json
import sys
from pathlib import Path

results_path, known_path = Path(sys.argv[1]), Path(sys.argv[2])
results = json.loads(results_path.read_text())
known = json.loads(known_path.read_text())
known_titles = {k["title"] for k in known["known_failures"]}

observed_titles = set()  # every tier2 spec title seen at all in this run
failing_titles = set()   # tier2 spec titles whose outcome was 'unexpected'


def walk(suites):
    for suite in suites:
        for spec in suite.get("specs", []):
            for t in spec.get("tests", []):
                if t.get("projectId") != "tier2":
                    continue
                observed_titles.add(spec["title"])
                # Playwright's JSON reporter status is one of skipped/expected/
                # unexpected/flaky. 'unexpected' is the only one that means "this
                # test's final outcome did not match its expectedStatus" — i.e. a
                # real failure, exhausting retries where configured.
                if t.get("status") == "unexpected":
                    failing_titles.add(spec["title"])
        walk(suite.get("suites", []))


walk(results.get("suites", []))

newly_red = sorted(failing_titles - known_titles)
now_green = sorted((known_titles & observed_titles) - failing_titles)
not_run = sorted(known_titles - observed_titles)

ok = True

if newly_red:
    ok = False
    print(f"\n{len(newly_red)} Tier 2 test(s) failed that known_failures.json does NOT list:")
    for t in newly_red:
        print(f"  ! {t}")
    print("  Either a genuine new regression, or known_failures.json needs a new entry.")

if now_green:
    ok = False
    print(f"\n{len(now_green)} known-failure test(s) ran and PASSED:")
    for t in now_green:
        print(f"  FIXED? {t}")
    print("  Remove the entry from known_failures.json if the underlying defect is genuinely fixed.")

if not_run:
    ok = False
    print(f"\n{len(not_run)} known-failure test(s) were not observed in this run at all:")
    for t in not_run:
        print(f"  ? {t}")
    print("  A filtered run, a renamed test, or a serial-mode file that aborted before reaching it.")
    print("  Investigate rather than ignore -- an unobserved known failure is not a confirmed one.")

if ok:
    print(f"\ntier2 failures match known_failures.json exactly ({len(known_titles)} known failure(s))")
else:
    sys.exit(1)
PY
