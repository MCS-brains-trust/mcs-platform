# E2E Tier 2 — deep accounting flows

Design, 2026-08-09. Follows the Tier 1 route sweep (`e2e/README.md`), which crawls
213 read-only routes and deliberately excludes 185 that mutate state, cost money per
call, or have effects that leave this machine. Tier 2 covers a subset of those.

## Purpose

Tier 1 catches crashes. It cannot catch a route that returns 200 while writing the
wrong number, which is the defect class this codebase actually suffers from — GST-free
income reported as taxable, a comparative column built from movement instead of closing
balance, depreciation calculated but not posted. Tier 2 exercises complete accounting
journeys and asserts the resulting figures.

Scope for this batch is two flows: **year-end close** and **roll-forward with
comparatives**. BAS/GST and document generation are deferred.

## Decisions

| Decision | Choice | Why |
|---|---|---|
| What is asserted | Depth: real flows, real figures | Breadth POST-smoke would find the same crash class Tier 1 already covers |
| Metered/external calls | Real, not stubbed | Matches the existing `playwright.config.ts` rationale; exercises real provider contracts |
| FuseSign | Carved out — blanked in `settings_e2e` | The E2E database is a production copy, so a live send would dispatch real signature envelopes to real client addresses. Irreversible, and listed as such in `e2e/README.md` |
| Test subject | Seeded fixture entity | Deterministic figures that survive a `refresh_e2e_db.sh`, so the golden file changes only when code changes |
| Assertion style | Blessed golden baseline plus rule-based invariants | The golden file catches drift nobody thought to assert; the invariants state the accounting rules, so a wrong-but-consistent figure cannot be blessed into permanence |

Xero, MYOB and QuickBooks need no carve-out: `e2e_harden.sql` blanks their OAuth
tokens on restore, so live calls cannot authenticate regardless. Email needs none
either — `settings_e2e` falls back to a locmem backend unless `E2E_EMAIL_HOST` is set.

In practice the two flows in this batch are entirely internal: trial balance import,
depreciation and roll-forward call no third-party service. So the live-calls decision
and the FuseSign carve-out govern Tier 2 from here on but change nothing about the two
specs below. The carve-out is worth making now regardless, because it closes a live
production credential that `settings_e2e` currently inherits.

## Isolation

Each spec file gets its own Django instance and its own database branch, because both
flows perform one-way transitions (finalising a year, rolling forward) that no cleanup
can undo. `fixtures/instance.ts` selects port `8200 + workerIndex`, invokes the existing
`scripts/start_server.sh <db> <port>` unchanged, waits on `/accounts/login/`, and tears
down by killing the server and dropping the branch.

Two hazards the per-file model introduces:

**Shared Redis keyspace.** Every instance reads `E2E_CACHE_URL`, defaulting to redis
DB 4, and `SESSION_ENGINE` is `cached_db`. Two parallel instances would therefore share
a session keyspace, and a session created against instance A's database could be served
from cache by instance B — which branched before that session row existed. Fix: add
`KEY_PREFIX` to the `settings_e2e` cache config, fed by an `E2E_CACHE_PREFIX` variable
that `start_server.sh` sets to the database name.

**Login.** Tier 1 reuses `storageState` from `global-setup.ts`. Tier 2 must not: cookies
ignore port, so a Tier 1 cookie would be *sent* to a Tier 2 instance and fail in a way
that reads as a permissions bug. Tier 2 logs in per instance through the real TOTP form,
using a `fixtures/login.ts` helper extracted from `global-setup.ts` so both tiers share
one implementation.

Celery needs no handling in this batch. `core/views.py` contains no `.delay()` calls,
and all four target views are synchronous. `CELERY_TASK_ALWAYS_EAGER` will be required
before document-generation specs land.

## Components

| Path | Purpose |
|---|---|
| `e2e/fixtures/instance.ts` | Per-file Django instance and database branch, with teardown |
| `e2e/fixtures/login.ts` | TOTP login helper shared by both tiers |
| `core/management/commands/e2e_seed_fixture_entity.py` | Seeds the deterministic subject entity |
| `core/management/commands/e2e_dump_figures.py` | Canonical JSON dump of TB, journals, depreciation schedule and FS lines |
| `e2e/tier2/yearend_close.spec.ts` | TB import through to statements |
| `e2e/tier2/roll_forward.spec.ts` | Finalise, roll forward, reroll diff and apply |
| `e2e/tier2/figures.baseline.json` | The blessed golden file |
| `e2e/scripts/bless_figures.sh` | Manual bless, mirroring `bless_baseline.sh` |

Both management commands call `assert_e2e_database()` and refuse to run outside a
database carrying the `e2e_marker` table, matching every existing `e2e_*` command.

### The fixture entity

A company with:

- a prior year, finalised, carrying known closing balances on balance-sheet accounts
- a current year, draft, ready to receive a trial balance
- one fixed asset partway through its life, so depreciation is non-zero in both years
- a general pool with an opening balance

Seeded idempotently, so it can be re-run after any database refresh.

### `e2e_dump_figures`

Emits canonical JSON for one financial year: decimals as strings to avoid float drift,
keys sorted, rows ordered by account code. Covers trial balance lines (including
opening, prior and adjustment columns), adjusting journals with their lines, the
depreciation schedule, and financial-statement line items. Takes a `--checkpoint` label
so a spec can dump several times within one flow.

## Spec 1 — `yearend_close.spec.ts`

Flow: upload TB → `review_tb_import` → `commit_tb_import` → map unmapped accounts →
depreciation schedule → `depreciation_post_to_tb` → `financial_statements_view`.

`commit_tb_import` reads its staged payload from `request.session["staged_tb_import"]`,
which is the flow the LocMemCache defect broke: under multiple gunicorn workers a stale
worker copy made the staged payload look absent, and the view's response to that is the
message `No staged TB import data found.` This spec asserts that message never appears,
making it a direct regression test for commit `41c8773`.

Invariants, each taken from the contract the view states in its own docstring:

1. A trial balance out of balance by more than $0.02 is refused, and no
   `TrialBalanceLine` rows are written.
2. A difference within tolerance is refused without `rounding_acknowledged` and accepted
   with it.
3. A committed trial balance has total debits equal to total credits.
4. `depreciation_post_to_tb` is idempotent: pressing it twice with an unchanged schedule
   produces a second figures dump identical to the first. This is the highest-value
   assertion in the batch, because the reverse-then-repost logic is where stacking bugs
   live.
5. Posting depreciation leaves opening and rollover balances untouched.
6. Posting without `confirmed=1` in the POST body changes nothing.

Golden checkpoints: `after_tb_commit`, `after_depreciation_post`.

## Spec 2 — `roll_forward.spec.ts`

Flow: finalise the fixture's current year → `roll_forward` → inspect the new year →
`reroll_forward_diff` → `reroll_forward_apply`.

1. Roll-forward is refused while the source year is unlocked, asserted before finalising.
2. Every balance-sheet account's opening balance in the new year equals its closing
   balance in the prior year. This is the invariant behind commits `f12a48d` and
   `de7d04d`; it is absolute and needs no baseline.
3. Net profit or loss, less tax, is closed to retained earnings. P&L accounts carry as
   comparatives, not as opening balances.
4. `reroll_forward_diff` on a freshly rolled year returns an empty diff.
5. After mutating one prior-year closing balance, the diff reports exactly that account
   with the correct `difference`, and applying it moves only that line.
6. Rolling forward twice does not create a duplicate year.

Golden checkpoint: `after_roll_forward`.

## Failure diagnosis

A golden mismatch that printed a whole-document JSON diff would be unreadable. The
comparator reports the first 20 differing entries as
`checkpoint: account — expected X, got Y`, so a stacking regression surfaces as
`after_depreciation_post: 6-1200 expected 12,450.00 got 24,900.00` and names itself in
the failure line.

Instance teardown drops the branch in an `afterAll` that runs on failure as well as
success. A killed run cannot wedge the next one regardless, because `start_server.sh`
already drops the branch `WITH (FORCE)` on the way in.

## Blessing

`bless_figures.sh` mirrors `bless_baseline.sh`: run the specs, review the diff, promote
deliberately. Never automatic. Rule-based invariants are not blessable — they either
hold or the run fails.

## Out of scope

BAS/GST worksheet flows; document generation (trust minutes, beneficiary statements,
engagement letters, the legal wizard); the remaining metered and external routes beyond
what these two flows touch; `CELERY_TASK_ALWAYS_EAGER`, deferred until docgen specs need
it.
