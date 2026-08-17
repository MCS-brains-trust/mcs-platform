# Strict financial-year resolution: a transaction posts to the year its date falls in, or it does not post

**Date:** 2026-08-17
**Status:** design approved, not implemented
**Raised by:** Elio, on being told he would need to create FY2027 before uploading a bank
statement that runs to 31 July 2026 — "That is not practical."

## The defect

`core/txn_periods.py:resolve_fy_for_txn` answers "which financial year does this transaction
post to?" for the whole application. When no open year covers the transaction's date it falls
back to the most recent open year:

```python
txn_date = parse_txn_date(txn.date)
if txn_date:
    for fy in fys:
        if fy.start_date <= txn_date <= fy.end_date:
            return fy
return max(fys, key=lambda f: f.end_date)      # ← the fallback
```

So a transaction dated **15 July 2026**, with no FY2027 yet, posts into **FY2026**. That
overstates FY2026, corrupts the June BAS, and misstates the year's financial statements. The
transaction's date is not in doubt — the application knows exactly when it happened and chooses
the wrong year anyway.

There is a second, less obvious route to the same place. `entity_financial_years` returns only
years with status `draft`, `in_review` or `finished`, so **finalised years are not in the
postable set**. A transaction dated inside a finalised year therefore also fails to match and
also hits the fallback. The 2026-08-17 estate audit listed 18 finalised years across 8 entities,
so this is the more likely form to already exist in data.

### Why this matters more than it looks

The fallback is not a stray line. Its docstring justifies it explicitly, on the grounds that the
rebuild must reproduce whatever posting did:

> The rebuild must reproduce this exactly, fallback included. Filtering on the date range instead
> would drop every unparseable-date transaction out of the year posting put it in, and the
> rebuild would then zero lines it had legitimately created — turning the rebuild into the data
> loss it exists to prevent.

That reasoning is sound and is preserved here. What it conflates is two different situations,
which this design separates:

| Situation | What we know | Decision |
|---|---|---|
| Date unparseable | nothing to reason from | **keep the fallback** |
| Date parses, falls in a finalised year | exactly when it happened | **do not post** |
| Date parses, falls in no year at all | exactly when it happened | **do not post** |

## Decisions taken

Each was put to Elio on 2026-08-17 and is recorded here with the alternatives that were
rejected, so a later reader does not have to re-litigate them.

**1. An unpostable transaction confirms normally, posts nowhere, and says why.**
Chosen over refusing the confirm, auto-creating the year, and filtering at import. The point is
that Elio can allocate a whole statement in one sitting and lodge the June BAS without creating
FY2027 first; the July rows then post themselves to FY2027 the moment that year exists, because
posting resolves by transaction date rather than by the job's year
(`review/views.py:_post_confirmed_txn_to_tb`). Rejected: refusing the confirm moves the same
friction from upload to confirm; auto-creating a financial year as a side effect of clicking an
account is too surprising given what a year carries (status, opening balances, roll-forward);
bounding the import alone leaves the fallback intact for every other path.

**2. Finalised years and missing years are both refused, with different messages.**
One rule, two explanations — only the second is resolved by creating a year. Rejected: fixing
only the missing-year case leaves the defect in the form most likely to be in the data already;
allowing postings into finalised years lets a correction reopen a closed, lodged year by the back
door, which is worse than the bug.

**3. Unparseable dates keep the fallback.** Scoped deliberately. Changing it would make a
mangled date block a transaction from ever reaching the ledger, possibly with no way to fix the
date in the UI, and unreadable-date rows are the ones most likely to be already posted through
the fallback — which would reopen the historical-data question. See "Out of scope".

**4. No new model field, no migration.** `is_confirmed=True` with `posted_to_tb=False` already
represents "confirmed but not posted" — posting returns `False` today when it cannot resolve a
year. The reason is derivable from the date and the entity's years, so it is computed when needed
rather than stored.

## Design

### The rule

`core/txn_periods.py:resolve_fy_for_txn` — the unparseable branch moves above the loop, and the
fallback stops applying to dates that parse:

```python
txn_date = parse_txn_date(txn.date)
if not txn_date:
    # Nothing to reason from. Unchanged behaviour, deliberately: see decision 3.
    return max(fys, key=lambda f: f.end_date)
for fy in fys:
    if fy.start_date <= txn_date <= fy.end_date:
        return fy
return None
```

`None` already means "do not post" to every caller: `_post_confirmed_txn_to_tb` returns `False`
without posting when the resolution is `None`. No caller needs changing for correctness.

### Why the rebuild stays safe

This is the property that makes the change viable, and it holds by construction rather than by
keeping two rules in step. Five call sites share this one function:

| Consumer | Uses it to | Effect of `None` |
|---|---|---|
| `review/views.py:_post_confirmed_txn_to_tb` | decide where a confirm posts | returns `False`, posts nothing |
| `core/views.py:_bank_tb_totals` (`:1205`) | decide which transactions a year's figures aggregate | excluded from every year |
| `core/views.py` (`:1279`) | rebuild a year's `bank_statement` rows | contributes nothing |
| `core/views.py` (`:10888`) | group the bank contra | contributes nothing |
| `core/views_bas.py` | rebuild after a reallocation | rebuild skipped, correctly — the transaction belongs to no year |
| `core/txn_periods.py:resolve_bas_period_for_txn` (`:120`) | find the BAS period for the amended flag | returns `None`, so `flag_period_amended` flags nothing |

Because the aggregation and the posting path ask the same question, a transaction that returns
`None` is both never posted and never aggregated. The rebuild therefore cannot zero a line for a
transaction it also refuses to post. The docstring's data-loss scenario is avoided not by keeping
the fallback but by both sides agreeing.

The last row is a consequence worth stating rather than discovering: an unpostable transaction
will not mark a lodged BAS period as amended. That is correct — it changed no ledger and no
lodged figure, so there is nothing to flag — but it means the amended badge and the unpostable
badge are answering different questions and neither implies the other.

### The reason, derived not stored

New in `core/txn_periods.py`:

```python
def unpostable_reason(txn) -> str | None
```

Returns `None` when the transaction resolves to a year. Otherwise it examines **all** of the
entity's financial years — not only the postable ones, which is what distinguishes the two cases —
and returns a message:

- a year covers the date but is finalised → `"FY2023 is finalised and cannot receive postings"`
- no year covers the date → `"No financial year covers 15 Jul 2026 — create FY2027 to post it"`

Called only when posting was skipped, so the ordinary path pays for no extra query.

### Surfacing

A transaction that confirms without posting must say so at the moment it happens, and keep saying
so afterwards:

1. **`confirm_transaction`'s JSON response** gains `posted` (bool) and `post_warning` (string, `""`
   when posted). The review page shows the reason on the row immediately, with no reload.
2. **The review row** renders a badge whenever `is_confirmed` and not `posted_to_tb`, carrying the
   same reason, so it survives a reload and is visible to whoever opens the job next.
3. **`bas_reallocate_transaction` and `bas_bulk_reallocate`** return the same warning field, since
   a reallocation can move a transaction's tax treatment but never its date — a transaction that
   was unpostable stays unpostable, and the screen should not imply otherwise.

No count or dashboard rollup in this change. If a job routinely carries unpostable rows, that is
worth surfacing later; adding it now is speculative.

### The BAS needs no change

Checked, not assumed. `_confirmed_transactions` (`core/bas_utils.py`) selects candidates by the
job's financial year, which is a different rule from posting's — but `calculate_gst_for_period`
then bounds them by a date window, passing `period_start or fy.start_date` and
`period_end or fy.end_date`. So even the full-year view is bounded by the year's own dates, and a
15 July 2026 transaction cannot reach an FY2026 BAS through any period selection. A
finalised-year-dated transaction is excluded the same way.

This is worth an explicit test rather than a comment, because the two attribution rules coexisting
is exactly the kind of thing that drifts.

## Pre-flight: confirm the historical claim before the rule ships

Elio's position is that no transaction has already posted into the wrong year. That is likely
right for the year-end-spanning case, which needs an unopened future year to arise at all. It is
less obviously right for the finalised-year case, given 18 finalised years in the book.

**The implementation plan must include, as its first step, a read-only probe** over every entity
reporting each posted transaction whose date falls outside the year it posted to, split into
"date falls in a finalised year" and "no year covers the date". If it returns zero the assumption
is recorded as fact and the change proceeds. If it returns anything, **stop** — the change would
make the rebuild exclude those transactions and zero the trial-balance lines they created, and
that is a decision about historical client ledgers, not an implementation detail.

The probe is read-only and cheap. It is a gate, not a formality.

## Testing

**Unit — `core/tests_txn_periods.py`**, one per resolution case:

| Date | Years available | Expected |
|---|---|---|
| inside an open year | FY2026 open | FY2026 |
| inside a finalised year | FY2023 finalised, FY2026 open | `None` |
| covered by no year | FY2026 open only, date 15 Jul 2026 | `None` |
| unparseable | FY2026 open | FY2026 (fallback, unchanged) |

Plus `unpostable_reason` returning the finalised message, the missing-year message, and `None`
for a postable transaction.

**Integration — confirm path:**

- Confirming a transaction dated 15 Jul 2026 with no FY2027: response 200, `posted: false`, a
  warning naming the date, and **no `TrialBalanceLine` created or changed**.
- Creating FY2027 and confirming the same transaction: it posts to FY2027, and FY2026's figures
  are untouched.
- A transaction dated inside a finalised year: same shape, finalised message.

**Regression — the rebuild:**

- A year holding an unpostable confirmed transaction rebuilds without zeroing anything, and the
  transaction contributes nothing.
- The existing rebuild tests in `core/tests_bank_tb_rebuild.py` stay green, including the
  unparseable-date fallback case, which this change must not alter.

**End to end:** extend `e2e/tier2/bank_to_bas_flow.ts` only if the fixture can carry an
out-of-year transaction without disturbing the pinned BAS figures. The fixture's six transactions
all land in October and the figures are hand-computed, so an added transaction changes them. If
that proves awkward, the Django integration tests are sufficient and the e2e suite is left alone —
say so rather than blessing new figures to accommodate a test.

## Out of scope

- **Unparseable dates** keep the fallback (decision 3).
- **No auto-creation of financial years.**
- **No change to the import period filter.** `review/views.py:1241-1291` already filters imported
  lines to a supplied period and logs what it excluded; defaulting that period to the job's
  financial-year range is a reasonable follow-up but is not needed for correctness once posting is
  strict, and it would silently drop statement lines that reconciliation depends on.
- **No BAS attribution change.** `_confirmed_transactions` selecting by job year while posting
  selects by date is a genuine inconsistency, but the date window makes it harmless today. Worth
  its own spec if it ever stops being harmless.
- **No repair of historical wrong-year postings.** If the pre-flight probe finds any, that is a
  separate piece of work with its own accounting decisions.

## Success criteria

1. A transaction dated outside every open year confirms, posts nothing, and states why — in the
   confirm response and on the row.
2. The same transaction posts to the correct year once that year exists, with no re-allocation.
3. No trial-balance line is created in a year the transaction's date does not fall in.
4. The rebuild zeroes nothing it should not: the full Django suite shows no new failures against
   the recorded baseline.
5. Unparseable-date behaviour is bit-for-bit unchanged.
6. The pre-flight probe's result is recorded in the implementation plan, whatever it says.
