# Closing the BAS-to-trial-balance desync

## Purpose

Five defects let an entity's BAS and its trial balance disagree with each other. All five
are in `main` today, all five affect live client data, and none is caught red by any test —
the Tier 2 bank-to-BAS suite documents the first three rather than covering them, and the
last two were found while resolving this spec's open implementation risk.

1. **An unsynchronised write drops postings.** `set_gst_treatment` reads a transaction
   with no lock and saves the whole row, so it can overwrite `is_confirmed` and
   `posted_to_tb` back to their pre-confirm values.
2. **Correcting a confirmed transaction never re-posts the trial balance.**
   `confirm_transaction` guards posting on `posted_to_tb`, so a second confirm updates the
   transaction and skips the ledger.
3. **BAS reallocation posts nothing to the trial balance at all.**
   `bas_reallocate_transaction` and `bas_bulk_reallocate` update the transaction's
   confirmed fields and never call any posting helper.
4. **The bank contra is grouped by a different year from the posting it mirrors.**
   `_recalc_bank_contra` scopes on `job__financial_year` while posting resolves the year
   from the transaction's own date, so a statement that spans a year end posts to one year
   and lands in the other year's bank contra.
5. **Bank postings can land inside a journal adjustment row.** `_get_or_create_tb_line`
   falls through to `qs.first()` when an account has no non-adjustment row, so on an
   account that carries only journal adjustments the bank postings accumulate into one of
   them. Two live entities are in this state today (see below). It is invisible on screen
   and it is what makes wiring the rebuild dangerous.

The BAS reads a transaction's own confirmed fields (`core/bas_utils.py`); the financial
statements read the trial balance. After any correction or reallocation the two disagree,
silently and indefinitely.

This spec covers those five defects and nothing else. Two sibling projects — coverage for
the other eight bank parsers, and the bank-to-BAS flow for trust, partnership and sole
trader — each get their own spec.

## What the discovery found

**The rebuild primitive already exists, unused.** `_recalculate_bank_tb_lines`
(`core/views.py:1150`) rebuilds every `source='bank_statement'` trial-balance line by
aggregating the entity's posted transactions, and deliberately leaves `manual_journal`
lines alone. It is called from nowhere. Verified 2026-08-16: the only occurrence of the
name in the codebase is its own `def`.

**It is also incomplete for this purpose.** Every branch does
`.filter(...).first()` followed by `if tb_line:`, so it only updates lines that already
exist. Two consequences, and a reallocation produces both:

- It never **creates** a line. Reallocating to an account with no trial-balance line yet
  silently drops the new posting.
- It never **zeroes a vacated** line. Reallocating away from an account leaves that
  account absent from `account_totals` entirely, so the loop never visits it and its old
  balance stands forever.

**And it aggregates the wrong set of transactions.** `core/views.py:1164-1168` filters on
`job__entity=fy.entity` and nothing else — no financial-year filter of any kind. It sums
every posted transaction the entity has ever had, across every year, onto whichever `fy`
it was handed. On a single-year entity this is invisible. On a multi-year book, wiring
this to run on every correction would collapse all years into one on the first edit anyone
makes. This is the most dangerous of the three gaps and the least visible.

So the plan is not "call the existing helper". It is "finish the existing helper, prove it,
then call it". That ordering is the single most important constraint in this document:
once wired, the primitive runs on every edit of every book.

**Three functions disagree about which year a transaction belongs to.** Posting resolves
the financial year from the transaction's own date (`review/views.py:119-140`).
`_recalc_bank_contra` uses `job__financial_year`. The rebuild, as above, uses neither. A
statement that spans a year end therefore posts to one year and is counted into another's
bank contra. A rebuild whose whole purpose is to reproduce posting cannot use a different
rule from posting, so this has to be settled before anything is wired.

**The two bank-contra writers do not double-apply, but they do disagree.**
`_recalc_bank_contra` (`core/views.py:10557`) already runs after every confirm, and the
rebuild computes bank totals too. Both SET rather than accumulate, so running both is
idempotent and nothing double-counts. They differ in three ways, which makes the resulting
bank line depend on which ran last:

| | `_recalc_bank_contra` | rebuild's bank block |
|---|---|---|
| `closing_balance` | `dr − cr` | `ob + dr − cr` |
| scope | `job__financial_year` | entity-wide, no filter |
| line absent | creates it, consolidates duplicates | skips it |

The `opening_balance` divergence is dormant rather than live: `source='bank_statement'`
lines carry an opening balance of zero, because the prior-year rollover writes
`source='rollover'` lines instead (`core/views.py:2862,2872`) and a bank account's brought-
forward balance lives on its own non-bank-statement line (`core/views.py:2232-2236`).
Dormant is not fixed — it becomes live the moment anything stamps an opening balance onto
a bank-statement line.

**The rebuild assumes a partition that posting does not honour.** The rebuild reads and
writes only `source='bank_statement', is_adjustment=False` rows, and deliberately leaves
`manual_journal` alone. That is only safe if every bank posting is in a `bank_statement`
row. Posting does not guarantee it: `_get_or_create_tb_line` (`core/views.py:850-856`)
takes `qs.filter(is_adjustment=False).first()` **or `qs.first()`**, so an account holding
nothing but journal adjustments receives its bank postings into one of those adjustment
rows.

Queried against production on 2026-08-16, two entities are already in that state:

| Entity | Account | Rows present | Posted transactions on that code |
|---|---|---|---|
| Veronica Cerratti Pty Ltd | 3565 | 3 × `manual_journal` (adjustment), no `bank_statement` | 72, $419,356.03 |
| Daniel Habteslassie | 4080 | 1 × `manual_journal` (adjustment), no `bank_statement` | 25, $237,464.00 |

Habteslassie's journal row carries Dr 246,536.00 against 237,464.00 of posted bank
transactions, which is the mechanism showing through in the arithmetic.

Wired as originally specced, the first correction anyone makes to Veronica Cerratti would
create a fresh `bank_statement` line holding the whole $419,356.03 while the journal rows
keep theirs, and the account would roughly double. The rebuild's own `manual_journal`
exclusion — written as a safety property — is what fires the corruption. No test in
workstream 1 would catch it: "`manual_journal` lines untouched" passes precisely while this
happens.

**Incremental posting cannot be reused for corrections.** `_post_txn_to_tb`
(`core/views.py:1038`) accumulates with `+=` onto existing lines. Re-posting a corrected
transaction would double-count it. Rebuild-from-source is not a stylistic preference here;
it is the only approach that is correct against an accumulating ledger.

## Decisions

**Rebuild from source, rather than reverse-and-repost.** After any correction the
transaction set is the authority and the bank-statement trial-balance lines are derived
from it. This repairs already-corrupted entities the next time anyone edits them, and
there is no reversal bookkeeping to drift. The costs are accepted: it is O(all posted
transactions) per edit, and it depends entirely on the aggregation rule being right, which
is why that rule gets its own tests before anything calls it.

**One rule for which year a transaction belongs to: the year covering its date.** Posting
already uses it, and the rebuild has to match posting or it cannot reproduce it. So
`_recalc_bank_contra` moves off `job__financial_year` onto the same date-derived rule, and
all three paths agree. This changes live behaviour on the four confirm call sites and the
Recalc Contra button for any entity whose statements cross a year end — that is the point:
those entities are the ones whose contra is wrong today. Accepted deliberately rather than
deferred, because leaving one of three functions on a different rule is how this class of
defect regrows.

**One implementation of the bank contra.** The rebuild's `bank_totals` block is deleted and
`_recalculate_bank_tb_lines` calls `_recalc_bank_contra(fy)` instead. `_recalc_bank_contra`
is the exercised implementation — four confirm call sites plus a user-facing button — and
it already creates a missing line and consolidates duplicates, which the rebuild's block
does not. Keeping one writer removes the order-dependence outright instead of making two
writers agree and hoping they stay agreed.

**Bank postings own their own rows.** Posting stops falling through onto whatever row
exists and always targets a `source='bank_statement', is_adjustment=False` row, creating
one when there is none. This makes source the partition the rebuild already assumes, and it
is the only change here that touches the posting path itself. Trial-balance reads already
sum across the rows an account holds — 96 account codes in production carry more than one
row today, routinely mixing `tb_import`, `rollover` and `manual_journal` — so adding a
`bank_statement` row beside a journal row changes no reported figure.

**The two entangled entities are repaired by hand before the rebuild is wired.** This is
the one exception to "audited, never repaired automatically", and it is not an exception to
the rule so much as an instance of it: the audit command reports the entanglement, and a
person decides what each account should read. The sequencing is what matters — fixing
posting stops new entanglement, but it does not unpick the $419k and $237k already sitting
in journal rows, and the rebuild must not run on either entity until someone has.

**The rebuild refuses to run on an entangled entity.** Detection stays in the code
permanently, not just until the two known entities are repaired: if a posted transaction's
account code has no `bank_statement` row but does have some other row, the rebuild logs and
declines rather than creating a duplicate. Those corrections behave as they do today —
desynced, which is the defect this project fixes — and that is the correct failure. A
rebuild that corrupts a ledger is worse than one that declines to run on it.

**Corrections inside a lodged period are allowed, and flagged.** Blocking them and
requiring an unlodge would give a cleaner audit trail, but it removes a workflow the BAS
detail tabs offer today. Instead the correction proceeds, the trial balance rebuilds, the
lodged snapshot stays frozen, and the period is marked as amended since lodgement so the
divergence is visible rather than silent.

**Existing client data is audited, never repaired automatically.** The sweep reports
variance and writes nothing. Some entities may have been compensated by hand by an
accountant, and no automated sweep can distinguish a defect-induced variance from a
deliberate correction. Which entities to repair is an accounting decision, made per entity
after reading the report.

**Fixing the race removes a load-bearing test constraint.** The Tier 2 suite currently
requires every account code in its `ALLOCATIONS` table to carry no mapped `tax_code`,
because an account whose tax code resolves fires `/gst-treatment/` concurrently with
`/confirm/` and triggers defect 1. That workaround is why the account picker's auto-apply
path has never been exercised at all. Once the race is fixed, an account with a mapped tax
code goes back into the table: the workaround becomes a regression test and the coverage
gap closes.

## The workstreams

Workstreams 0 and 1 gate the rest. Nothing is wired until the partition holds and the
primitive is proven.

The entanglement discovery adds a second gate that cuts across the order: workstream 4's
audit command, and the manual repair it feeds, both have to land before workstream 2 wires
the rebuild into any live path. So workstream 4 is built early even though it is numbered
last, and workstream 2 is the last thing merged.

### 0. Make bank postings own their own rows

`_post_txn_to_tb` and `_post_bank_contra_entry` stop calling `_get_or_create_tb_line`'s
fall-through and instead resolve a `source='bank_statement', is_adjustment=False` row,
creating one when absent. `_get_or_create_tb_line` keeps its current behaviour for its other
callers; the bank-posting paths get a narrower variant rather than a change underneath
everyone.

For an account that already has a `bank_statement` row this is a no-op — the row it picks
today is the row it will pick after. It differs only where that row is missing, which is
the entangled case, and there the new behaviour is what stops the entanglement growing.

### 1. Harden the rebuild primitive

`_recalculate_bank_tb_lines` gains the three missing behaviours: create a
`source='bank_statement'` line for an account that has postings but no line, zero any
existing `source='bank_statement'` line whose transactions have all moved away, and
aggregate only the transactions that belong to `fy`. The existing
`is_adjustment=False` and `source='bank_statement'` filters stay — journal and imported
lines remain untouched. Its `bank_totals` block goes, replaced by a call to
`_recalc_bank_contra(fy)`, whose own filter moves from `job__financial_year` to the same
date-derived rule.

Both of those need a transaction's string date resolved to a financial year, so the
resolution currently inline in `_post_confirmed_txn_to_tb` (`review/views.py:119-140`) —
the four-format parse and the FY range scan — is extracted into a shared helper first, and
the three functions call it. Workstream 3 is a fourth consumer: it maps the same string
date onto a BAS period at each correction call site. A second copy of that parsing is
exactly how these would drift apart again.

The rebuild also gains the entanglement guard: before writing anything it checks whether any
posted transaction's account code lacks a `bank_statement` row while carrying a row from
some other source, and if so logs the entity, the codes, and declines. The check runs on the
same aggregation pass, so it costs nothing extra.

The helper keeps the existing fallback behaviour for an unparseable date — most recent FY
— because narrowing it is a separate change with its own blast radius. This makes the
filter a per-transaction question, not a date-range query: the rebuild keeps a transaction
when `resolve_fy(txn) == fy`, not when its date falls inside `fy`'s range. The two are the
same for a parseable date and different for the fallback, and using the range would drop
every unparseable-date transaction the posting path had placed in the most recent year —
turning the rebuild into the data loss it exists to prevent.

### 2. Fix the race, then wire the rebuild

`set_gst_treatment` and `bulk_set_gst_treatment` (`review/views_enhanced.py`) adopt the
pattern `confirm_transaction` already uses: `select_for_update()` inside
`transaction.atomic()`. The full-row `txn.save()` becomes `save(update_fields=[...])`
scoped to the GST fields — the lock is what makes it correct, the narrowed save is what
limits the damage if another path ever races.

The rebuild is then called from every path that can change an already-posted transaction:

| Call site | File |
|---|---|
| `confirm_transaction`, when the row was already posted | `review/views.py` |
| `set_gst_treatment`, when the row was already posted | `review/views_enhanced.py` |
| `bas_reallocate_transaction` | `core/views_bas.py:861` |
| `bas_bulk_reallocate`, **once** after the loop | `core/views_bas.py:947` |

The double-post guard stays exactly as it is. It is correct for its own purpose, and the
Tier 2 test that depends on it must stay green.

The four confirm paths in `review/views.py` already call `_recalc_bank_contra` immediately
after posting (`:699,938,1035,2053`). Those calls stay and are harmless: the rebuild now
delegates its bank contra to the same function, and that function sets rather than
accumulates, so a second call in the same request recomputes the identical figure.

### 3. Amended-period flag

Three fields on `BASPeriod` (`core/models.py:3728`), one migration:
`amended_since_lodgement`, `amended_at`, `amended_by`. Set when a correction touches a
transaction whose date falls within a period whose `status` is `lodged`. Cleared on lodge,
because a fresh lodgement takes a fresh snapshot that incorporates the amendment;
preserved through unlodge, because that is when the audit trail matters most. Surfaced as a
badge beside the existing status colours (`core/views_bas.py:168`). The snapshot fields are
never written.

Periods are created lazily, so a transaction may fall in a date range with no `BASPeriod`
row. That is not a case to handle: no row means no lodgement, so nothing to flag.

### 4. Desync audit command

`manage.py audit_bank_tb_desync [--entity <pk>]`. For each entity and financial year it
compares the posted transactions against the `source='bank_statement'` trial-balance lines
and reports the variance per account. It writes nothing, and exits non-zero when it finds
variance so it can gate CI later.

It reports entanglement as its own category, separately from variance, because the two need
different responses: a variance is repaired by rebuilding, entanglement has to be unpicked
by a person before rebuilding is safe. The report names the entity, the account code, the
rows and their sources, and the posted total that should be sitting in a `bank_statement`
row. Its first real use is the two entities above.

It assigns each transaction to a year with the same resolution helper the rebuild uses, so
a transaction counts against exactly one year and a year-spanning statement is not reported
as variance in both. The report names the year each variance falls in.

It shares the aggregation function with the rebuild. Stated plainly because it bounds what
the command proves: it detects a trial balance that disagrees with its transactions, not an
aggregation rule that is itself wrong — both sides would be wrong identically. Workstream
1's tests are the only thing standing behind the rule. A second independent implementation
would cross-check it, at the cost of doubling the thing most likely to drift; one rule with
good tests is the better trade.

## Testing

**Django tests.** Run under the sqlite override, and judged against the existing failure
baseline rather than against zero.

1. Rebuild primitive: equivalence with incremental posting on a clean book; creates a
   missing line; zeroes a vacated line; idempotent under repeat; `manual_journal` lines
   untouched; `opening_balance` preserved.
2. Year isolation, the defect most likely to reach live data: an entity with two financial
   years and posted transactions in both, where rebuilding one year leaves the other's lines
   untouched and neither year absorbs the other's totals. Plus the year-end case that
   motivated the scoping decision — a job attached to FY2025 holding a July transaction —
   where posting, the rebuild and `_recalc_bank_contra` all place it in FY2026.
3. FY resolution helper: each of the four accepted date formats; an unparseable date
   falling back to the most recent year; and a transaction with an unparseable date staying
   in the rebuild of the year it was posted to rather than being zeroed out of it.
4. `set_gst_treatment` does not reset `is_confirmed` or `posted_to_tb`.
5. Both reallocation endpoints move the trial balance; the bulk endpoint rebuilds once,
   not once per transaction.
6. The amended flag sets inside a lodged period, stays clear outside one, and clears on
   re-lodge.
7. Posting into an account that holds only a journal adjustment row creates a
   `bank_statement` row rather than accumulating into the adjustment — the Cerratti shape,
   reduced to a fixture.
8. The rebuild declines on an entangled entity: nothing written, the entity and codes
   logged. Then, once the fixture is repaired, the same rebuild runs and balances.

**Tier 2 end-to-end,** added to `e2e/tier2/bank_to_bas_flow.ts`:

7. Correcting an already-confirmed transaction moves the trial balance, and the BAS
   reconciles to it.
8. Reallocating through the BAS detail tab moves the trial balance.
9. Allocating to an account with a mapped `tax_code` (`0510` Sales) posts exactly once.
10. Lodge, then correct: the period is flagged amended and the snapshot is unchanged.

**The lock cannot be proven by a Django test.** `select_for_update` is a no-op on sqlite,
so the sqlite-backed suite can prove the narrowed `update_fields` save and nothing about
the lock. Test 9 against real Postgres is the only evidence that defect 1 is fixed, and a
single green run is not evidence: the original failure was intermittent, observed
fail/pass/fail across three consecutive runs. The gate is a soak of ten consecutive runs of
that spec, at `--workers=1`, off-peak because production shares this host. The soak is a
one-off acceptance gate, not an addition to the standing suite.

## Documentation this invalidates

These currently describe the three defects as known and accepted. Leaving them in place
would be worse than having no note at all, so rewriting them is part of the work:

- `e2e/README.md`, limits 3, 4 and 5, and the load-bearing `ALLOCATIONS` comment
- the `ALLOCATIONS` comment in `e2e/tier2/bank_to_bas_flow.ts`
- `docs/superpowers/specs/2026-08-14-bank-to-bas-tier2-design.md`, "what this does not
  cover"

## What this does not cover

- The other eight bank parsers, which still run flat text extraction plus per-line regex
- Entity types other than company
- The AI suggestion path, including `accept_all_suggestions`
- Any repair of existing client data: workstream 4 reports and never writes. The two
  entangled entities are repaired by hand, off the report, as a prerequisite to wiring —
  not by anything this project ships

## Verification

Complete when all of these hold:

1. The eight Django test groups pass, with no new failures against the baseline.
2. `bank_to_bas_company.spec.ts` passes at `--workers=1`, including the four new tests.
3. Ten consecutive runs of that spec pass with a mapped-tax-code account in `ALLOCATIONS`.
4. The existing Tier 2 suite still passes and `known_failures.json` stays empty.
5. Tier 1's 215 tests still pass.
6. `audit_bank_tb_desync` runs clean against the Tier 2 fixture entity after the suite.
7. `audit_bank_tb_desync` over all entities reports zero entanglement — which means
   Veronica Cerratti 3565 and Daniel Habteslassie 4080 have been repaired by hand and
   signed off, not that the check was relaxed.
8. Every document listed above is updated, with no stale claim that these defects are
   accepted.

Gates 7 and the rebuild wiring are ordered: no correction path calls the rebuild on `main`
until gate 7 holds.
