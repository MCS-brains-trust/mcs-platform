# Closing the BAS-to-trial-balance desync

## Purpose

Three defects let an entity's BAS and its trial balance disagree with each other. All
three are in `main` today, all three affect live client data, and none is caught red by
any test — the Tier 2 bank-to-BAS suite documents them rather than covering them.

1. **An unsynchronised write drops postings.** `set_gst_treatment` reads a transaction
   with no lock and saves the whole row, so it can overwrite `is_confirmed` and
   `posted_to_tb` back to their pre-confirm values.
2. **Correcting a confirmed transaction never re-posts the trial balance.**
   `confirm_transaction` guards posting on `posted_to_tb`, so a second confirm updates the
   transaction and skips the ledger.
3. **BAS reallocation posts nothing to the trial balance at all.**
   `bas_reallocate_transaction` and `bas_bulk_reallocate` update the transaction's
   confirmed fields and never call any posting helper.

The BAS reads a transaction's own confirmed fields (`core/bas_utils.py`); the financial
statements read the trial balance. After any correction or reallocation the two disagree,
silently and indefinitely.

This spec covers those three defects and nothing else. Two sibling projects — coverage for
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

So the plan is not "call the existing helper". It is "finish the existing helper, prove it,
then call it". That ordering is the single most important constraint in this document:
once wired, the primitive runs on every edit of every book.

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

## The four workstreams

Workstream 1 gates the rest. Nothing is wired until the primitive is proven.

### 1. Harden the rebuild primitive

`_recalculate_bank_tb_lines` gains the two missing behaviours: create a
`source='bank_statement'` line for an account that has postings but no line, and zero any
existing `source='bank_statement'` line whose transactions have all moved away. The
existing `is_adjustment=False` and `source='bank_statement'` filters stay — journal and
imported lines remain untouched.

The multi-format date parsing currently inline in `_post_confirmed_txn_to_tb`
(`review/views.py:94`) is extracted into a shared helper. Workstream 3 needs to map a
transaction's string date onto a BAS period at each of the four correction call sites, and
a second copy of that four-format parsing is exactly how the two would drift apart.

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

**Open implementation risk.** `_recalc_bank_contra` (`core/views.py:10557`) also writes
bank contra lines and is already called after every confirm. The rebuild computes bank
totals too. Whether these two agree, conflict, or double-apply must be settled during
implementation before both run on the same path.

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
2. `set_gst_treatment` does not reset `is_confirmed` or `posted_to_tb`.
3. Both reallocation endpoints move the trial balance; the bulk endpoint rebuilds once,
   not once per transaction.
4. The amended flag sets inside a lodged period, stays clear outside one, and clears on
   re-lodge.

**Tier 2 end-to-end,** added to `e2e/tier2/bank_to_bas_flow.ts`:

5. Correcting an already-confirmed transaction moves the trial balance, and the BAS
   reconciles to it.
6. Reallocating through the BAS detail tab moves the trial balance.
7. Allocating to an account with a mapped `tax_code` (`0510` Sales) posts exactly once.
8. Lodge, then correct: the period is flagged amended and the snapshot is unchanged.

**The lock cannot be proven by a Django test.** `select_for_update` is a no-op on sqlite,
so the sqlite-backed suite can prove the narrowed `update_fields` save and nothing about
the lock. Test 7 against real Postgres is the only evidence that defect 1 is fixed, and a
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
- Any repair of existing client data: workstream 4 reports and never writes

## Verification

Complete when all of these hold:

1. The four Django test groups pass, with no new failures against the baseline.
2. `bank_to_bas_company.spec.ts` passes at `--workers=1`, including the four new tests.
3. Ten consecutive runs of that spec pass with a mapped-tax-code account in `ALLOCATIONS`.
4. The existing Tier 2 suite still passes and `known_failures.json` stays empty.
5. Tier 1's 215 tests still pass.
6. `audit_bank_tb_desync` runs clean against the Tier 2 fixture entity after the suite.
7. Every document listed above is updated, with no stale claim that these defects are
   accepted.
