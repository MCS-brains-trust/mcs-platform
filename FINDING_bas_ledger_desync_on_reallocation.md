# Finding: changing a confirmed transaction desynchronises the BAS from the ledger

**Found:** 2026-08-14, during Tier 2 bank-to-BAS e2e work (branch `feat/tier2-bank-to-bas`).
**Status: FIXED, 2026-08-17, on branch `fix/bas-tb-desync-impl`.** Unmerged at the time of
writing — merging to `main` auto-deploys, so the fix is not in production until Elio ships
it. The body below is kept verbatim as the historical record of what was wrong; do not read
it as a description of current behaviour.
**Severity:** correctness, user-facing, financial. No data was lost by the reporting
desync, but the third defect below did lose posted ledger entries.

## How each defect was closed

| Defect | Fix |
|---|---|
| Path 1 — re-confirming on the review screen never re-posts | `f7dbdbc` — an already-posted row takes a rebuild path instead of no path. Rebuild rather than re-post, because `_post_txn_to_tb` accumulates with `+=` |
| Path 2 — BAS reallocation has no posting logic at all | `3f5585f` — both endpoints rebuild; single returns 409 if the rebuild declines, bulk rebuilds once per year rather than once per transaction |
| Third defect — `set_gst_treatment`'s unlocked full-row save | `5af720d` — `atomic()` + `select_for_update()` + `save(update_fields=[...])` on both GST treatment endpoints |
| Fourth defect — three different financial-year rules | `34d9e2c` — one shared rule in `core/txn_periods.py`, every consumer asks it |
| The divergence being silent | `f00846c` — `BASPeriod.amended_since_lodgement` and a badge on the period strip, so a correction inside a lodged period is visible |

Supporting work in the same series: `d9a478b` partitions bank postings from journal rows so
they can no longer accumulate inside a `manual_journal` adjustment row, `47394d7` finishes
the rebuild primitive, and `f527ef5` adds `manage.py audit_bank_tb_desync`.

Evidence: `review/tests_correction_reposts.py`, `core/tests_bas_reallocate_posting.py`,
`review/tests_gst_treatment_race.py`, `core/tests_bas_amended_flag.py`, and four end-to-end
tests in `e2e/tier2/bank_to_bas_flow.ts`. The lock specifically is evidenced only by the
ten-run Postgres soak — `select_for_update` is a no-op on sqlite, so no Django test can
prove it.

**The auto-confirm question raised at the end of this document was not resolved.**
`selectAccount` still auto-confirms on an account click. The race that made it dangerous is
fixed and the path is now covered by the e2e suite, but whether clicking an account *should*
post an entry before the user has touched the tax dropdown remains a design decision nobody
has made.

## Summary

Once a bank-statement transaction has been confirmed and posted, **changing its account code
or tax type updates the transaction but never re-posts the trial balance.** The BAS is
computed from the transaction records and the financial statements are computed from the
trial balance, so after any such change the two disagree — with no warning on either screen.

Two independent paths reach this state. Neither re-posts.

## Path 1 — re-confirming on the review screen

`review/views.py:660-689`. A second confirm updates everything on the transaction and saves
it:

```python
txn.confirmed_code = confirmed_code
txn.confirmed_tax_type = confirmed_tax_type
...
txn.calculate_gst(tax_type=txn.confirmed_tax_type, is_gst_registered=is_gst)
txn.confirmed_gst_amount = ...
txn.save()

# Post to trial balance (expense/income + GST + bank contra).
# Re-check the freshly-locked row's posted flag before posting (A18).
if not txn.posted_to_tb:          # <-- already True, so this never runs again
    _post_confirmed_txn_to_tb(txn)
```

The `posted_to_tb` guard is correct for its stated purpose — it stops a double-click
double-posting (A18), and `select_for_update` at `:654` makes that race-safe. But it also
means a *corrected* confirm is silently not re-posted. The guard cannot distinguish "post
this twice" from "this changed, post it again".

## Path 2 — reallocating from the BAS screen

`core/views_bas.py:913-923`. This path has no posting logic at all:

```python
txn.confirmed_code = new_code
txn.confirmed_tax_type = new_tax
txn.calculate_gst(...)
txn.confirmed_gst_amount = txn.gst_amount
txn.is_confirmed = True
txn.save()
```

No `_post_confirmed_txn_to_tb`, no removal of the superseded `TrialBalanceLine` rows, no
recalculation. `bas_bulk_reallocate` immediately below shares the shape.

## Why the two reports diverge

- The **BAS** reads `PendingTransaction` records — `core/bas_utils.py:140,147`. It sees the
  correction.
- The **trial balance / financial statements** read `TrialBalanceLine`. It does not.

So after a correction the BAS is right and the ledger is stale. That is the exact failure the
code's own comments already warn about, in both files:

> `review/views.py:644-646` — "Rejecting legacy tax codes outright would break older clients;
> storing them verbatim is what desynchronised the BAS from the ledger."

> `core/views_bas.py:900-902` — "this endpoint used to persist any string it was given, which
> is how chart-of-accounts tax codes ('GST', 'INP') ended up in confirmed_tax_type and
> desynchronised the BAS from the ledger."

Both comments describe a *vocabulary* desync that was fixed. The *posting* desync underneath
it was not.

## How a user hits this without doing anything unusual

The review screen makes the first, wrong posting happen automatically — the user does not
opt into it:

1. `selectAccount()` (`templates/review/review_detail.html:914`) is called when an account is
   clicked.
2. It calls `applyAccountGST()`, which **sets the tax dropdown's value** from the account's
   default `tax_code` (`:883`).
3. It then calls `tryAutoConfirm()` (`:928`), which fires `confirmTransaction()` as soon as
   both account and tax are non-empty (`:943`).

So clicking an account immediately confirms and posts, using that account's default tax
treatment, before the user has touched the tax dropdown. If that default is wrong for the
transaction — a GST-free item under a GST-coded account, say — the user's correction lands in
the transaction and never reaches the ledger.

The auto-apply is skipped only when the account's `tax_code` is empty, or is outside
`taxCodeToTaxType`'s map at `:853-867` (that map omits `FOA`, `IOA` and `FCA`, which several
real accounts carry). Whether a user gets a premature posting therefore depends on which
account they happen to pick.

## A third defect, found while working around the first two

`review/views_enhanced.py:558-599` — `set_gst_treatment` — is a **lost-update race that can
silently un-confirm and un-post a transaction.**

It loads the row with a plain `get_object_or_404(PendingTransaction, pk=pk)` at `:565`: no
`select_for_update`, no `transaction.atomic()`. It then mutates several fields and calls a
full `txn.save()` at `:598`, which writes **every** column back from its in-memory copy —
including `is_confirmed` and `posted_to_tb`.

The page fires this endpoint concurrently with `/confirm/`. `applyAccountGST` POSTs to
`/gst-treatment/` at `templates/review/review_detail.html:896` while `tryAutoConfirm` POSTs to
`/confirm/` a few lines later (`:928`, `:943`). If `set_gst_treatment` reads the row before
`confirm_transaction` commits, its later save reverts `is_confirmed` and `posted_to_tb` to
their pre-confirm values. The transaction silently becomes unconfirmed and its trial-balance
entries are orphaned.

The irony is worth stating plainly: `confirm_transaction` takes `select_for_update` inside an
atomic block specifically to make confirmation race-safe (`review/views.py:651-655`, citing
A18) — and that lock is defeated by its own page, which fires an unlocked full-row save at the
same moment.

**Observed, not theorised.** Running the Tier 2 suite three times against the same code gave
fail / pass / fail. Each failure showed the same signature: `#confirmed-count` fluctuating,
and the bank contra losing an entire 1,100.00 credit — ending at Dr 3,000 / Cr 872 instead of
Dr 4,100 / Cr 872. Only the transactions whose account carried a mapped `tax_code` were
affected, because those are the only ones that trigger the parallel `/gst-treatment/` call.

This one is not a reporting mismatch. It loses a posted entry from the ledger.

## Suggested direction

Not implemented — this needs a decision about intended behaviour, and it touches financial
posting, so it wants its own tests.

For the third defect, the minimum is to give `set_gst_treatment` the same
`transaction.atomic()` + `select_for_update()` treatment `confirm_transaction` already has,
and to narrow its write to the fields it owns (`update_fields=[...]`) so it cannot clobber
`is_confirmed` / `posted_to_tb` regardless of ordering. Both are small and independent of the
larger question below.

The shape of a fix is to make correction a first-class operation rather than a second confirm:
reverse the existing `TrialBalanceLine` rows for the transaction and post the new ones inside
the same `transaction.atomic()` block that already holds `select_for_update`, keying off
"has anything that affects posting changed?" rather than off `posted_to_tb`. Both paths should
call it, so the review screen and the BAS screen cannot drift apart again.

Worth deciding separately: whether `selectAccount` should auto-confirm at all, or only
pre-fill the dropdowns and leave confirmation to the user. The auto-confirm is what turns a
wrong default into a posted entry.

## Evidence trail

Every reference below was read directly, not inferred.

| Claim | Location |
|---|---|
| Re-confirm updates the transaction, then skips posting | `review/views.py:660-689` |
| The guard and its lock | `review/views.py:651-655`, `:687` |
| BAS reallocation never posts | `core/views_bas.py:913-923` |
| BAS is computed from `PendingTransaction` | `core/bas_utils.py:140,147` |
| Account click sets the tax dropdown | `templates/review/review_detail.html:873-885` |
| Account click then auto-confirms | `templates/review/review_detail.html:914-929, 937-944` |
| Tax codes that skip auto-apply | `templates/review/review_detail.html:850-869` |

Reproduced during Task 6 of the Tier 2 plan: allocating `FRESH FOOD SUPPLIES` (GST-free) to an
account whose default is GST-coded posted a GST-inclusive entry that no later correction could
undo. The failure was deterministic and reproduced twice at the same point.
