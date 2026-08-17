# StatementHub E2E regression suite

Playwright suite running against a disposable full copy of production, on this droplet.

## Quick start

```bash
cd /opt/statementhub/e2e
npm test -- --project=tier1        # full Tier 1 sweep (~3 min)
npm run report                     # open the HTML report
```

The suite provisions its own database and Django instance; nothing needs to be
running first.

## Why it is built this way

**A disposable copy, not production.** Cleanup on production cannot be made reliable:
entity deletion cascades through 14+ relationships and `OfficerDistributionHistory.officer`
is `PROTECT`, so a teardown hits `ProtectedError` and strands data — and tests crash
mid-run by nature. Some effects cannot be undone by any cleanup at all (a sent FuseSign
envelope, a sent email), and financial-year status is one-way, so a test that finalises
a year could not put it back. Instead the whole database is thrown away.

**Real production data, not synthetic fixtures.** `refresh_e2e_db.sh` restores an actual
production dump, so the suite runs against real chart-of-accounts shapes and the messy
edge cases that break document generation. All five entity types and all three
financial-year statuses are represented.

**Regression, not absolute correctness.** Route statuses are baselined and any *change*
fails. The manifest resolves URL parameters by picking a row of the right model, which
cannot know a view's preconditions — an RDTI route handed a year with no RDTI data
returns 404 and is right to. Demanding 200 everywhere would mean permanent known
failures, and a suite with those trains everyone to ignore it.

## Isolation

Production runs on this same host, so every shared resource is deliberately separated.
The Celery boundary is the one that matters most: production's worker is live on redis
DB 0 and the default queue, so an E2E task enqueued with the defaults would be
**executed by the production worker against the production database**.

| | Production | E2E |
|---|---|---|
| Database | managed DO Postgres 18.4 | local PG 18 cluster, port 5433 |
| Celery | redis DB 0, default queue | redis DB 3, queue `e2e` |
| Cache/sessions | redis DB 1 | redis DB 4 |
| Media | `/opt/statementhub/media` | `.e2e/media` (mirrored copy) |
| Email | Resend HTTP API | sandbox SMTP, or locmem if unconfigured |
| Xero/MYOB/QB | live OAuth tokens | tokens severed on restore |

Two guards make this fail closed rather than fail quietly:

- `config/settings_e2e.py` refuses to import unless the target database is loopback
  **and** carries the `e2e_marker` table written by `e2e_harden.sql`.
- Every `e2e_*` management command re-checks that marker itself, because the failure
  being guarded is someone omitting `--settings=config.settings_e2e`.

Both are tested: pointed at production, they refuse and create nothing.

**OAuth tokens are severed on restore.** Providers rotate refresh tokens single-use, so
if the E2E instance refreshed an inherited token, the copy still held in production
would be invalidated and the live connection would break. `e2e_harden.sql` blanks the
named tables plus any `access_token`/`refresh_token` column it finds — the generic sweep
already caught `integrations_qbtenant`, which the explicit list had missed.

## Refreshing the data

```bash
bash /opt/statementhub/scripts/refresh_e2e_db.sh
```

Dumps production read-only, restores into `sh_e2e_template`, severs credentials,
mirrors media, verifies, and seals the template. Test files then branch from it with
`CREATE DATABASE ... TEMPLATE`, a local file copy taking ~4s for this 500MB database —
which is what makes a fresh database per file practical.

Run it when you want fresher data; it is not needed per run.

## The baseline

`tier1/status.baseline.json` records the expected HTTP status per route, keyed on name
**and** path pattern — `entity_detail` is two different routes (`/entities/<pk>/` at 200
and the token-authenticated `/api/coworker/entity/<pk>/` at 401), and keying on name
alone made the second inherit the first's expectation.

```bash
npm test -- --project=tier1
npm run bless        # promote observed statuses, after reading the diff
```

Bless in the same breath as the run that produced the figures. Playwright wipes
`test-results/` at the **start** of every run, and both blessing scripts read their
observed figures out of it — so running `--project=tier1` and then `--project=tier2`
destroys `observed-status.json` before `npm run bless` ever sees it, and the reverse
order destroys the Tier 2 observations the same way. Run one project, bless it, then
run the other.

Blessing is deliberately manual. A suite that rewrote its own expectations could never
detect a regression — a route that broke would be recorded as broken and reported as
passing from then on. 5xx responses are **refused** for the baseline and listed under
`known_failures` instead, so they keep failing while staying distinguishable from routes
nobody has reviewed.

## Regenerating the route manifest

```bash
cd /opt/statementhub
python3 manage.py e2e_dump_routes --settings=config.settings_e2e
```

`tier1/routes.manifest.json` is committed on purpose: a new application route appears as
a diff, which is what keeps coverage honest instead of decaying silently.

Excluded routes are **not** coverage. They are grouped by why the crawler stays away —
`external` (leaves the machine), `metered` (costs money per call), `mutating` (would make
later assertions order-dependent). Those are Tier 2's job.

## Current coverage

- 435 application routes (Django admin's 432 are reported separately, being third-party)
- 213 crawlable, 210 excluded by category, 12 unresolved
- Tier 1: 215 tests, all passing (verified 2026-08-13). 213 route statuses are
  baselined and `known_failures` is empty — every route the crawler reaches is at its
  blessed status, so any change is a regression.
- Tier 2: 45 tests asserting figures, not statuses — year-end close, roll-forward across
  four entity types (company, trust, partnership, sole trader), and a bank-statement-to-BAS
  flow, each with its own deterministic fixture, Django instance and database branch

### Tier 2 fixtures

The roll-forward flow lives in `tier2/roll_forward_flow.ts` and is called once per
entity type by a thin spec file. Each type exists because its equity structure differs,
which is where the defects have been:

| Type | Account holding the year's result | Section | Sub-coded capital accounts |
|---|---|---|---|
| company | `3-1000` Retained earnings | `equity` | no |
| trust | `4199` Undistributed income | `pl_appropriation` | yes, per beneficiary |
| partnership | `4199` Unappropriated profits | `capital_accounts` | yes, per partner |
| sole_trader | `4199` Undistributed income | `capital_accounts` | no |

The company fixture is the odd one out on purpose: its chart uses MYOB-style hyphenated
codes, and no entity in the production copy has one. That mismatch is what exposed the
retained-profits defect. The other three use the HandiLedger numerics every real entity
actually carries, modelled on a named exemplar entity each.

Run one profile with `npm run test:tier2 -- tier2/roll_forward_trust.spec.ts`. Seven spec
files (six roll-forward/year-end plus bank-to-BAS, below) each boot a Django instance and
a ~471 MB database branch, so use `--workers=2` for a full-tier run — production shares
this host.

Each spec file owns a fixed port so two files running on different workers cannot
collide:

| Spec file | Port |
|---|---|
| `yearend_close.spec.ts` | 8201 |
| `roll_forward.spec.ts` (company) | 8202 |
| `roll_forward_trust.spec.ts` | 8203 |
| `roll_forward_partnership.spec.ts` | 8204 |
| `roll_forward_sole_trader.spec.ts` | 8205 |
| `bank_to_bas_company.spec.ts` | 8206 |
| `instance.smoke.spec.ts` | 8209 |

### Bank-to-BAS fixture

`tier2/bank_to_bas_flow.ts` (called by `tier2/bank_to_bas_company.spec.ts`) takes a
synthetic CBA bank statement through to a lodged BAS: upload and import, debit/credit
sign fidelity, allocation and posting to the trial balance, the double-post guard, BAS
labels against hand-computed figures, the GST identity, the coverage gate at both UI and
server level, lodgement with an override reason and its frozen snapshot, and the unlodge
permission guard at both UI and server level. The fixture is `e2e/fixtures/statements/
cba_sample.pdf`, generated by `make_cba.py` — it is synthetic, not a real client
statement, precisely so it can be committed.

**One bank, one entity type.** This covers CBA only, company only. `entity_type` reaches
the GST path only to select a chart of accounts (`core/bas_utils.py:341-345`); the
arithmetic is driven by tax codes on transactions, so a second entity type would mostly
re-prove the same sums. **The other eight bank parsers this application supports are
uncovered by this suite entirely.** They still carry whatever parsing defects predate
this work; nothing here exercises them, and nothing here should be read as "bank
statements are tested" in general — only CBA is. Building out a fixture for another bank
needs a real exemplar statement from that bank (the same reason only CBA has one so far),
plus a new fixture generator and a spec file like this one.

Adding another entity type is more than a new fixture profile plus a spec file — three
things in `bank_to_bas_flow.ts` are hard-coded to the company profile and would all need
revisiting:

- **`ALLOCATIONS`'s account codes** (`0510`, `0602`, `1685`, `1545`, `1126`, `0578`) come
  from the global, entity-type-scoped `ChartOfAccount` template
  (`review/views.py:553-555`), not from the fixture's own chart — a different entity type
  may not carry these codes at all. There is no longer a constraint that they carry no
  mapped `tax_code`: `0510` Sales deliberately carries `GST` so the auto-apply path is
  exercised, and the table's fourth column records the value each account should
  auto-apply. A tripwire in the allocation loop asserts that value, so an account losing
  or gaining a `tax_code` fails loudly rather than silently changing what is covered.
- The bank account name string (`Cash at bank`) asserted after the wizard mapping step is
  hard-coded.
- The `2000` trial-balance row the double-post and TB-balances tests key on
  (`IDS.bank_account_code`) is this fixture's own chart code, not necessarily another
  profile's.

None of this is lifted into `BankToBasOptions` here, deliberately: doing so with no second
profile to validate against would be speculative.

**The AI suggestion path is excluded, deliberately.** Classification runs and is waited
on to completion (proving the classify step itself works), but the test then reloads the
page before allocating. The AI writes its suggested tax type into each row's `.tax-select`
client-side the moment a batch lands, and clicking an account option auto-confirms with
whatever is in that field — so without the reload, what actually posts is decided by a
metered, non-deterministic AI call rather than by this file's own allocation table, and a
regression in the deterministic figures below would be invisible. The reload clears the
unconfirmed client-side state and forces the hand-picked allocations to be what land in
the ledger.

Two further limits, neither fixable with a committed fixture (three more used to be listed
here and are all fixed — see "Previously documented here as accepted defects" below):

1. **The fixture assumes the kerning collapse rather than reproducing it.** Dates are
   stored as glued literals (`02Oct`) and drawn with a single call. That is the input
   shape the geometry parser expects, so it is correct — but a regression in how real-world
   kerning is handled would not be caught here. Only a real PDF could catch that, and a
   real client statement cannot be committed.
2. **The fixture carries no per-transaction running balance,** because a balance on every
   row out-populates the real debit and credit clusters in `_money_columns` and gets
   mistaken for a money column. Real statements have that column. The parser reads no
   balance from transaction rows, so nothing under test observes the difference — but the
   fixture is that much less like the real thing, and a reader comparing the two should
   know why.
## Previously documented here as accepted defects — all three now fixed

This section used to carry limits 3, 4 and 5, each describing a live application defect
that the suite documented rather than covered. All three are fixed, and the text is kept
because a reader of an older commit will find those limits and needs to know they were
closed, not quietly dropped.

**3. The allocation accounts were chosen to avoid a race — no longer.** Every code in
`ALLOCATIONS` used to carry no `tax_code`, or one absent from `taxCodeToTaxType`'s map
(`review_detail.html:853-867`), so `applyAccountGST` returned early and the account
picker's auto-apply path was never exercised at all. That was not cosmetic: when an
account's `tax_code` *did* resolve, selecting it fired two concurrent writes to the same
transaction — `selectAccount`'s own confirm, and a parallel `/gst-treatment/` call that
loaded the row with a plain `get_object_or_404`, no `select_for_update`, no atomic block,
and saved every column back from its stale copy. Measured, not theorised: with `0510` in
the table the suite failed fail/pass/fail across three runs, the transaction silently
un-confirmed and its posting missing from the trial balance.

`set_gst_treatment` and `bulk_set_gst_treatment` now take `transaction.atomic()` +
`select_for_update()` and save with `update_fields` naming only the fields they own, so
neither confirmation flag can be clobbered whatever the ordering. `0510` is back in
`ALLOCATIONS` and the auto-apply path is covered again. Note what still cannot be proven
by a Django test: `select_for_update` is a no-op on sqlite, so the **soak** below — this
spec run ten consecutive times against Postgres — is the only evidence for the lock
itself.

**4. Correcting an already-confirmed transaction never re-posted the trial balance.**
`confirm_transaction` guarded posting on `posted_to_tb`, which is correct for stopping a
double-click double-post and cannot distinguish "post this twice" from "this changed, post
it again". A corrected confirm updated the transaction and left the ledger holding the
original figures, so the BAS (which reads the transaction's own confirmed fields) and the
financial statements (which read the trial balance) disagreed after any correction.

An already-posted row now takes a rebuild path instead of no path. Covered by
*correcting a confirmed transaction moves the trial balance* above, and the double-post
guard still holds — *re-confirming a transaction does not post it twice* is unchanged and
still green.

**5. The BAS reallocation endpoints posted nothing to the trial balance at all.**
`bas_reallocate_transaction` and `bas_bulk_reallocate` updated the confirmed fields and
returned, calling no posting helper anywhere. Both now rebuild; the single endpoint returns
409 if the rebuild declines on an entangled account, and the bulk endpoint rebuilds once
per financial year rather than once per transaction. Covered by *reallocating from the BAS
screen moves the trial balance* above.

## Running Tier 2 from a git worktree

Three things silently mislead if you skip them, and the first is the dangerous one because
it goes **green** while testing the wrong code:

1. **`STATEMENTHUB_ROOT` must point at the worktree.** `scripts/start_server.sh` does
   `cd "${REPO_DIR}"`, which defaults to `/opt/statementhub` — so without it the suite
   serves the production checkout and tells you nothing about your branch. Leave
   `STATEMENTHUB_RUNTIME_ROOT` alone: the venv and `.e2e` state belong to the machine, not
   to the checkout under test (see `fixtures/paths.ts`).
2. **The template database must carry your branch's migrations.** `start_server.sh`
   branches `sh_e2e_template` with `CREATE DATABASE ... TEMPLATE` and never runs `migrate`,
   so a migration that exists only on your branch is absent from every run and each query
   touching the new column 500s. Apply it once:
   `DJANGO_SETTINGS_MODULE=config.settings_e2e E2E_DB_NAME=sh_e2e_template python3 manage.py migrate`.
3. **`collectstatic` must have run in the worktree.** `settings_e2e` uses manifest static
   storage, and a fresh worktree has no `staticfiles/`, so every page 500s with
   `Missing staticfiles manifest entry for 'css/style.css'`.

```bash
cd e2e
STATEMENTHUB_ROOT=/path/to/worktree npx playwright test tier2/bank_to_bas_company.spec.ts --workers=1
```

## The soak

The `/gst-treatment/` race failed intermittently — fail/pass/fail across three consecutive
runs — so one green run is not evidence that it is fixed. Ten are the acceptance gate:

```bash
cd e2e
for i in $(seq 1 10); do
  echo "=== run $i ==="
  STATEMENTHUB_ROOT=/path/to/worktree npx playwright test tier2/bank_to_bas_company.spec.ts --workers=1 \
    || echo "RUN $i FAILED"
done
```

Ten passes. A single failure means the lock is not fixed — stop and reopen it rather than
re-running until it goes green. Run off-peak: production shares this host, and each pass
makes real AI classification calls, so ten passes is ten metered classification cycles.

This is a one-off gate, not part of the standing suite.

## Defects found by the suite

**All of these are fixed.** The list is kept because it is the evidence the suite pays
for itself, and because each entry says what a future regression on that route would
look like. Nothing here is currently red — `known_failures` is empty in both tiers.

Found by the Tier 1 route sweep, fixed in `41c8773` (2026-08-08):

| Route | Defect |
|---|---|
| `/office-admin/asic/` | `views_office_admin.py` sliced then filtered → `Cannot filter a query once a slice has been taken` |
| `/associates/<pk>/edit/` | reached `assoc.entity.pk`, but `ClientAssociate.entity` is nullable and **all 3,987 production rows are NULL** |
| `/notes/<pk>/` and `/edit/` | same pattern; **all 49 notes** have NULL entity |
| `/years/<pk>/general-pool/` | rendered `core/general_pool_detail.html`, which did not exist |
| legal doc wizard ×2 | uncaught JS `False is not defined` — a Python bool rendered into JavaScript |
| `/years/<pk>/partner-statements/` | uncaught JS `Cannot set properties of null (setting 'textContent')` |

The same commit also replaced the per-process LocMemCache (which could shadow the
database under multiple gunicorn workers and silently drop session-staged import
payloads) and added ABN/ACN/TFN check-digit validation.

Found by the Tier 2 accounting flows:

| Fixed in | Defect |
|---|---|
| `7e11395` (PR #32) | `coa_sections` was built from the entity-TYPE template and never consulted `EntityChartOfAccount`, so an entity with its own chart had every account classify as P&L and nothing carried an opening balance |
| `7e11395` (PR #32) | retained profits was identified by the numeric code `4199` alone, which a hyphenated MYOB/Xero chart never carries, so the year's result was closed into a synthesised line beside the entity's real equity account |
| `5d1ae91` (PR #34) | `_expected_next_year_openings` predicted no retained-profits line when the prior year carried none, so the reconciliation diff omitted it — reporting half an amendment and leaving the next year out of balance if applied |

Found by the Tier 1 sweep's console-error checks, fixed in `e4b63ab` (PR #33): the
review dashboard called the Airtable API on **every render** and logged
`404 ... api.airtable.com/v0//` — an empty base ID — a live external call with a 15s
timeout on a page load, for an integration that had never worked.

## Tier 2

```bash
npm run test:tier2
npm run bless:figures      # after reading the diff
```

Deep accounting flows against a seeded fixture entity, one Django instance and one
database branch per spec file — both flows perform one-way transitions that no cleanup
can undo. Assertions come in two kinds: rule-based invariants taken from the views'
own docstrings (a committed TB balances, depreciation posts idempotently, a new year's
opening balance equals the prior year's closing) and a manually blessed golden file,
`tier2/figures.baseline.json`, which catches drift nobody thought to assert. Only the
golden file is blessable; the invariants either hold or the run fails.

The fixture entity is seeded by `manage.py e2e_seed_fixture_entity` on every boot, so
its figures survive a `refresh_e2e_db.sh` and the baseline changes only when the code
does.

**Tier 2 is fully green, and that is the healthy state.** This section previously
described two intentionally-red tests — a depreciation idempotency failure in
`yearend_close.spec.ts` and a roll-forward classification failure in
`roll_forward.spec.ts`. Both defects have since been fixed in application code and
pinned by `core/tests_rollforward_classification.py` and
`core/tests_rollforward_retained_profits.py`; `tier2/known_failures.json` records the
same thing and its list is empty.

`known_failures.json` is the authority, not this file. `scripts/check_tier2_failures.sh`
compares the actual failing set against it in both directions, so a new failure *and* a
silently-fixed known one are both loud. An empty list means every Tier 2 test passes and
any failure is a regression — do not add an entry to quiet a red test.

## Layout

```
e2e/
  playwright.config.ts    tier1 shares one instance; tier2 gets one per file
  global-setup.ts         logs in each role through the real TOTP flow
  fixtures/roles.ts       per-role authenticated pages
  tier1/routes.spec.ts    the sweep
  tier1/routes.manifest.json
  tier1/status.baseline.json
  scripts/start_server.sh branch a database, seed fixtures, serve
  scripts/bless_baseline.sh
```

Login goes through the real two-step TOTP form rather than a forged session cookie, so
the gate in front of the whole application is exercised on every run. Codes are derived
with `otplib` from fixed secrets, which matter because a rotating secret would make a
failed run unreproducible.

Rate limiting is disabled in `settings_e2e` (login and TOTP are capped at 5/min per IP
and every request comes from 127.0.0.1); the limiter is covered by a Django unit test
instead, so the behaviour is still asserted somewhere.
