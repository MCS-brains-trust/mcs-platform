# Tier 2 — deterministic fixtures for trust, partnership and sole trader

Extends `2026-08-09-e2e-tier2-design.md`. That design established Tier 2 against a
single fixture entity; this one generalises it to the entity types whose equity
structure differs, which is where the roll-forward defects live.

## Purpose

Tier 2 asserts figures rather than statuses, and it does so against exactly one
subject: `E2E Fixture Holdings Pty Ltd`, `entity_type="company"`, SPFR. Trust,
partnership and sole trader have no deterministic fixture, so the flows that matter
most to them have never run end to end.

The gap is not theoretical. `_populate_rolled_forward_fy` closes the year's result
into a retained-profits account, and what that account *is* differs per type:

| Type | Account holding the year's result |
|---|---|
| Company | Retained earnings |
| Trust | Undistributed income (`4199`) |
| Partnership | Unappropriated profits (`4199`) |
| Sole trader | Undistributed income (`4199`) |

`core/tests_rollforward_retained_profits.py` covers those four at the Django unit
level against synthetic charts. Nothing exercises them through the real views, the
real UI and a blessed baseline.

## What the production copy actually contains

Established by read-only query against `sh_e2e_template` on 2026-08-12. These
findings drove the decisions below and are recorded because they are easy to assume
wrongly.

- **16 entities**: 8 company (one an empty shell), 4 trust, 2 partnership, 2 sole
  trader, 1 SMSF.
- **Every entity with a chart carries `4199`.** The four showing no `4199` have zero
  COA rows — they are empty `ZZTEST-*` shells, not counterexamples.
- **No hyphenated account code exists anywhere in the book.** All ~5,000 COA rows are
  HandiLedger numeric. The company fixture's MYOB-style `3-1000` chart models no
  current client.
- **Sub-coded capital accounts are the real complexity.** Trusts carry 202 sub-coded
  rows: beneficiary-level accounts at `.01`/`.02`, plus a parallel `9001–9008` series
  mirroring the `4000` series per beneficiary. Partnerships do the same per partner.

The consequence for risk framing: the retained-profits defect fixed in PR #32 could
not have bitten any entity currently on the platform, because all charted entities
carry `4199`. The fix is forward-looking — onboarding from MYOB/Xero will introduce
hyphenated charts — not a live bleed. Recorded here because the PR's wording implied
otherwise.

Separately: five `ZZTEST-*` entities exist in **production**, apparently from earlier
manual testing. Not addressed by this design; noted as the outcome the E2E
environment exists to prevent.

## Decisions

**Faithful-but-small fixtures.** Each fixture is modelled on a real exemplar entity
and reduced to ~12–15 accounts. Rejected alternatives:

- *Full-chart clones* (copy 400–500 real accounts, zero the figures): maximum realism,
  but an unreadable baseline diff, and the fixture drifts whenever the dump refreshes.
- *Structure-only fixtures* (capital accounts, nothing else): as the existing
  fixture's own comments record, a chart without real P&L makes both the
  net-profit-closing formula and the P&L-comparative check evaluate to a trivial
  `0 == 0`.

**Two beneficiaries/partners per fixture** — the minimum that makes a distribution
split non-trivial.

**The company profile does not change.** Its blessed checkpoints must stay valid, so
the refactor must leave its seeded rows byte-identical.

**SMSF is excluded.** Fund accounting diverges enough (member balances,
contributions, pensions, its own reporting framework) that it needs its own flows
rather than a reuse of these. It is a later slice.

## Components

### `FixtureProfile`

`core/e2e_fixture_data.py` currently exposes module-level `FIXTURE_IDS`,
`PRIOR_YEAR_TB` and `CHART_OF_ACCOUNTS`, read directly by a `seed_fixture_entity()`
that hardcodes the company.

Replace with a `FixtureProfile` dataclass carrying:

- `key` — `"company"`, `"trust"`, `"partnership"`, `"sole_trader"`
- `ids` — fixed UUIDs
- `entity_kwargs` — name, type, valid ABN/ACN/TFN, `financial_year_end`, framework
- `chart` — `(code, name, section)` triples
- `prior_year_tb` — `(code, name, debit, credit)` tuples, balanced
- `retained_profits_code` — the account the year's result must land in

`seed_fixture_entity(profile)` takes one and is idempotent as today. A `PROFILES`
dict keys them.

UUIDs extend the existing scheme by discriminator, preserving "a failure is traceable
to a known row":

| Profile | Prefix |
|---|---|
| company | `e2e00000-…` (unchanged) |
| trust | `e2e00001-…` |
| partnership | `e2e00002-…` |
| sole_trader | `e2e00003-…` |

### `e2e_seed_fixture_entity`

Grows `--profile` (repeatable; default all). Writes one manifest per profile —
`.e2e/fixture_entity_<key>.json` — with `.e2e/fixture_entity.json` retained as the
company's manifest so existing specs keep reading their current path.

### The three charts

Capital structure is taken verbatim from the exemplar. Balance-sheet and P&L codes
are taken from the same exemplar at implementation time; only the equity structure is
pinned here, because it is the part under test.

**Trust** — exemplar `E & J Chiaravalle Family Trust`

- `4000.01` / `4000.02` Opening balance - Beneficiary
- `4005.01` / `4005.02` Distribution for year
- `4199` Undistributed income

The parallel `9001–9008` mirror series is omitted unless the flow proves to read it.

**Partnership** — exemplar `D.P Vaughan & D Vriend`

- `4000.01` / `4000.02` Opening balance - Partner
- `4003.01` / `4003.02` Share of profit
- `4054.01` / `4054.02` Drawings
- `4199` Unappropriated profits

**Sole trader** — exemplar `Daniel Habteslassie`

- `4010` Capital contribution
- `4049` Share of profit
- `4080` Drawings
- `4199` Undistributed income

### Spec structure

`tier2/roll_forward.spec.ts` is 27KB hardcoded to one entity. Extract the flow into
`tier2/roll_forward_flow.ts` exporting `describeRollForward(profile)`; each entity
type gets a thin spec file that calls it.

One Django instance and one database branch per spec file, matching existing per-file
isolation. The company keeps port 8202; trust, partnership and sole trader take 8203,
8204 and 8205. 8201 (`yearend_close`) and 8209 (`instance.smoke`) are already in use.

This avoids both a 4× serial run inside one file and four copies of the flow.

### Baselines

Checkpoints are namespaced by profile: `trust:after_roll_forward`,
`partnership:after_roll_forward`. `bless_figures.sh` keys each observed file on its
own `checkpoint` field rather than its filename, so no script change is needed.
Company checkpoints keep their current unprefixed names, leaving the blessed baseline
valid.

## Defect handling

Fix in application code, pin with a Django unit test alongside
`core/tests_rollforward_retained_profits.py`, and keep `tier2/known_failures.json`
empty so "any failure is a regression" holds. One defect per commit and PR, in the
shape of PR #32.

**The escalation rule.** A contained failure — a missing name match, a bad
classification, an off-by-one — is fixed under that policy. If a failure instead
reveals that a whole capability is absent (for example, roll-forward having no
handling for beneficiary or partner sub-accounts at all), that is an unimplemented
feature rather than a defect. Work stops, and the finding goes back to the user with
an estimate of what building it would involve, rather than being absorbed into this
scope.

## Testing

Each profile must, through the real UI:

1. Refuse roll-forward while the source year is unfinalised.
2. Roll the finalised prior year forward, with every balance-sheet and capital
   account carrying its own prior closing balance.
3. Close the year's result into `retained_profits_code`, and into no other account —
   in particular, synthesise no second equity line beside it.
4. Carry P&L accounts as comparatives with a zero opening.
5. Report no drift from `reroll_forward_diff` immediately after a fresh roll.

Items 2 and 3 are absolute invariants and need no baseline. Item 4's figures are
compared against the blessed baseline.

## Risks

- **Host contention.** Production runs on this same host. Four concurrent Django
  instances plus four Postgres branches must stay within a bounded worker count.
- **Sub-account handling may be absent**, triggering the escalation rule above. This
  is the most likely way the work exceeds its scope.
- **Refactor risk to the company profile.** Mitigated by requiring its seeded rows to
  be unchanged, verifiable by re-running Tier 2 against the existing blessed baseline
  before any new profile is added.

## Out of scope

SMSF; document generation; bank statement parsing; ASIC lodgement; FuseSign; the
Xero/MYOB/QB integrations; the 8 existing red Tier 1 routes and the 6 defects already
documented in `e2e/README.md`.
