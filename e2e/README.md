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
- Tier 1: 207 passing, 8 failing on genuine defects (see `known_failures` and below)
- Tier 2: 32 tests asserting figures, not statuses — year-end close, plus roll-forward
  across four entity types (company, trust, partnership, sole trader), each with its own
  deterministic fixture, Django instance and database branch

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

Run one profile with `npm run test:tier2 -- tier2/roll_forward_trust.spec.ts`. Six spec
files each boot a Django instance and a ~471 MB database branch, so use `--workers=2`
for a full-tier run — production shares this host.

## Known defects found by the suite

All confirmed present in production, not artefacts of this environment:

| Route | Defect |
|---|---|
| `/office-admin/asic/` | `views_office_admin.py:353` slices then filters → `Cannot filter a query once a slice has been taken` |
| `/associates/<pk>/edit/` | `views.py:7615` does `assoc.entity.pk`, but `ClientAssociate.entity` is nullable and **all 3,987 production rows are NULL** |
| `/notes/<pk>/` and `/edit/` | same pattern, `views.py:7833`/`7855`; **all 49 notes** have NULL entity |
| `/years/<pk>/general-pool/` | renders `core/general_pool_detail.html`, which does not exist |
| legal doc wizard ×2 | uncaught JS `False is not defined` — a Python bool rendered into JavaScript |
| `/years/<pk>/partner-statements/` | uncaught JS `Cannot set properties of null (setting 'textContent')` |

Separately: the review dashboard calls the Airtable API on **every render** and logs
`404 ... api.airtable.com/v0//` — an empty base ID. Not a test failure, but a live
external call on a page load.

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

**Two Tier 2 tests are intentionally red**, each the last test in its file so
Playwright's serial mode (which skips every remaining test in a file after the first
failure) still runs everything else:

- `yearend_close.spec.ts` — "posting depreciation is idempotent and leaves opening
  balances alone". `depreciation_post_to_tb` posts an unbalanced reversal journal;
  pressing "Post to Trial Balance" twice leaves the trial balance out of balance
  rather than at a net-zero change.
- `roll_forward.spec.ts` — "every balance-sheet account should carry its prior-year
  closing balance forward, and the reroll diff should catch it if one later changes".
  `_is_balance_sheet_account` (core/views.py) has no classification path for an
  account whose code isn't in the internal numeric HandiLedger ranges, has no
  `mapped_line_item`, and isn't in the *entity-type* `ChartOfAccount` template (a
  different model from the entity's own `EntityChartOfAccount`, which is never
  consulted at all) — exactly this fixture's chart of accounts. Every account,
  including balance-sheet ones, is misclassified as P&L and rolls forward with a
  zeroed opening balance instead of carrying its closing balance; the same
  classification call inside `reroll_forward_diff` makes the reconciliation "Re-Roll
  Forward" modal blind to the resulting drift too, even after a genuine correction.

Neither is a rig problem — a red Tier 2 suite with exactly these two failures named is
the expected, healthy state until both are fixed in application code.

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
