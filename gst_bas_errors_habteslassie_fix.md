# GST/BAS errors — Daniel Habteslassie (FY2026) — root cause & fix

Investigated 2026-07-30. Entity `d82ed91d-63a3-459e-a03c-b7a2ac755d07`,
sole trader, GST-registered, quarterly BAS, FY2026 (1 Jul 2025 – 30 Jun 2026).

The client is a medical practice: 311 of 356 receipts are health-fund/Medicare
payments (BUPA, NIB, TUH, Medicare, Defence Health), which are GST-free medical
services under GST Act s38-7.

## What was wrong on screen

| Label | Was reported | After code fix | After data repair |
|---|---|---|---|
| G1 total sales | 359,752.87 | 327,136.06 | 327,136.06 |
| G3 GST-free sales | 0.00 | 284,533.11 | 326,168.06 |
| G6 taxable sales | 359,752.87 | 42,602.95 | 968.00 |
| **1A GST on sales** | **32,704.81** | 3,873.00 | **88.00** |
| 1B GST credits | 6,576.88 | 6,567.97 | 6,567.97 |
| **Net GST** | **26,127.93 payable** | 2,694.97 refund | **6,479.97 refund** |

The final 1A of $88.00 is exactly the GST actually booked against the three
taxable transactions on the file, and the sales detail tab now reconciles to it
to the cent.

The quarterly views were wrong in the opposite direction: Q1, Q2 and Q3 each
reported **1A = $0.00** despite $65k–$90k of sales, and the four quarters summed
to $4,251.49 of GST on sales against the full year's $32,704.81 — a $28,453
reconciliation gap on the same underlying data.

The sales detail tab disagreed with its own summary: it totalled **$88.00** of
GST against the headline 1A of $32,704.81.

## Root causes

1. **The full-year worksheet was computed from aggregated trial balance lines,
   not from transactions** (`core/bas_utils.py` `_calculate_gst_from_tb_lines`).
   All 356 receipts post to one account (0590 Professional fees) whose COA
   default tax code is `GST`. Aggregation collapses them into a single TB line
   stamped `tax_type='GST'`, destroying the per-transaction GST-free/taxable
   split — so 100% of income was reported taxable.

2. **The 11/10 gross-up fabricated GST** (`_classify_line`). Any GST-coded
   bank_statement line had its balance multiplied by 1.1. The correct gross is
   the actual bank statement amount. Applied to the aggregate it also grossed up
   the GST-free portion, adding $28,453 of income that never existed.

3. **Full-year and period views were two different calculations** over the same
   data (TB lines vs transactions), so quarters could not reconcile to the year
   even when every dollar came from bank statements.

4. **Only the long-form tax-type vocabulary was understood.**
   `_resolve_section_and_tax` recognised `"GST on Expenses"` etc. but not the
   COA/MYOB tax codes (`GST`, `INP`, `CAP`, `FRE`) that several unvalidated write
   paths store in `confirmed_tax_type`. Unrecognised values silently fell back to
   the account's default tax code, so an explicitly-coded transaction could be
   reported as the opposite treatment.

5. **Non-reportable amounts were on the worksheet.** `N-T` / `BAS Excluded`
   income landed in G1 and G3 (as "GST-free sales"), and such expenses in
   G11/G14, instead of being excluded.

6. **Unvalidated tax-type writes** let COA tax codes into
   `confirmed_tax_type` (`core/views_bas.py` `bas_reallocate_transaction` /
   `bas_bulk_reallocate` accepted any string; `core/views.py`
   `review_approve_selected` / `review_confirm_transaction` and
   `review/views.py` auto-confirm persisted the classifier's value verbatim).
   Those same paths then force `confirmed_gst_amount = 0.00` for anything that
   isn't one of the two canonical GST labels — so 42 income transactions worth
   $41,634.95 were stored as taxable with **zero GST recorded**.

7. **`gst_treatment` was written as `'gst'`**, which is not in
   `GST_TREATMENT_CHOICES` (`core/views.py` `TAX_CODE_TO_GST_TREATMENT`). Every
   downstream check compares against `'taxable'`. 400 live rows hold it.

## Fixes applied (code)

All in the BAS engine and the write paths that feed it:

- `core/bas_utils.py` — rewritten so the worksheet has **one** calculation with
  three clearly separated sources: bank statements (always from confirmed
  transactions, using each transaction's own tax treatment and its actual gross
  amount), posted adjusting journals in the window, and undated balances
  (imported/rollover TBs, full-year view only). Adds
  `normalise_tax_treatment()`; `_classify_line` now takes an explicit
  `gross_up` flag instead of inferring it from the source; `N-T`/`BAS Excluded`
  are excluded from the worksheet; capital acquisitions route to G10;
  transactions confirmed with no account code are surfaced in the excluded list
  instead of vanishing.
- `review/models.py` — `canonical_tax_type()` / `is_taxable_tax_type()`
  normalise either vocabulary onto `TAX_TYPE_CHOICES`.
- `core/views_bas.py`, `core/views.py`, `review/views.py` — the five write paths
  normalise before persisting and reject genuinely unknown tax types; the
  "is this taxable" checks now use `is_taxable_tax_type` instead of comparing
  string literals.
- `core/views.py` — `TAX_CODE_TO_GST_TREATMENT` writes `'taxable'`, not `'gst'`.
- `core/views.py` — the superseded, unrouted `gst_activity_statement` /
  `gst_activity_statement_download` are marked as such, since they still carry
  the defective aggregate+gross-up logic and must not be re-wired as-is.

`core/tests_bas_gst.py` — 17 new tests. 9 of them reproduced the live defects
before the fix (full-year G1 4,730.00 vs 4,400.00 correct, detail/summary
disagreement, quarters not reconciling, legacy codes dropped entirely).

## Verified

- Live FY2026 recalculated: 1A = 3,873.00, 1B = 6,567.97,
  net = **2,694.97 refundable**; quarters now sum to the full year (1c rounding).
  These match a figure independently derived straight from the 467 confirmed
  transactions.
- Detail tabs now reconcile to the summary (3,873.02 vs 3,873.00).

## Impact on other clients

Every financial year on the platform was recalculated before and after the
change (pristine worktree at `d4156b9` vs fixed tree, same live DB). **6 of 23
financial years moved**; all six are corrections:

| Entity | FY | Change | Why |
|---|---|---|---|
| Daniel Habteslassie | 2026 | 1A 32,704.81 → **3,873.00**; net 26,127.93 payable → **2,694.97 refundable** | the reported defect |
| **Veronica Cerratti Pty Ltd** | 2026 | 1A **0.00 → 10,514.19**; net 11,889.26 refund → **1,608.93 refund** | same defect, opposite direction — all income was being reported GST-free |
| D.P Vaughan & D Vriend | 2025 | G11/G14 −5,000 each; 1A −0.75, 1B −0.01 | N-T expense removed from the worksheet (no GST effect); actual gross instead of net×1.1 |
| Hazaway Operations | 2024 | G1/G3 +1,943 (1A unchanged) | GST-free amount now reported rather than omitted |
| Hazaway Operations | 2025 | G11/G14 −3,382.10 each (1B unchanged) | N-T expenses removed from the worksheet |
| The Chiaravalle Family Trust | 2025 | G11/G14 −1,771 each (1B unchanged) | N-T expenses removed from the worksheet |

**Veronica Cerratti Pty Ltd needs the same review as Habteslassie** — its BAS was
reporting $0 GST on sales against $115,656 of taxable sales, a $10,280 swing in
net GST. The new figure is corroborated by a calculation taken straight from its
637 confirmed transactions (1A = 10,518.19 vs the worksheet's 10,514.19; the
difference is the ÷11 worksheet method vs summing per-transaction GST).

The remaining 17 financial years are unchanged.

## Separate problem found during verification (pre-existing, not caused by this fix)

**D.P Vaughan & D Vriend FY2025 has 582 transactions marked confirmed with no
account code**, totalling **$158,498.77**, all of it GST-bearing — roughly
**$14,409 of input tax credits never claimed**. They also have
`posted_to_tb=False`, so they are missing from the trial balance and P&L too, not
just the BAS.

This is a data problem in that client's file, not a calculation bug: the old code
skipped such transactions silently in both the full-year and period views, so the
figures have not changed. What is new is that they are now listed in the BAS
"excluded" panel with the reason "No account code — not included in any BAS
label", instead of vanishing. The `confirmed_code` guard added in the previous
review session prevents new ones being created.

(1 further uncoded transaction, $330.00 and GST-free, on Habteslassie FY2026.)

## Data repairs applied (2026-07-30, confirmed with the client's accountant)

Both clients are medical professionals, so the great majority of their income is
GST-free under s38-7.

### Daniel Habteslassie — 42 transactions restored to GST-free
`data_fixes/fix_habteslassie_gst_free_income.py` (snapshot:
`habteslassie_gst_free_income_snapshot.json`).

42 income transactions ($41,634.95) carried `confirmed_tax_type='GST'` while
also carrying `gst_treatment='gst_free'` and `confirmed_gst_amount=0.00`. They
are Medicare, BUPA, HCF, Medibank, GMHBA, Australian Unity, Teachers Health,
Defence Health, Monash Health and Tyro patient settlements — the same payers as
the 311 receipts already treated as GST-free. Only 3 transactions on the file are
genuinely taxable (sleep-clinic reporting fees, $968.00 with $88.00 of GST
actually booked); they were left alone.

Because these already posted their full gross to income with nothing to the GST
control account, the trial balance needed no adjustment — and the ~$3,785 of
apparent income overstatement resolves itself, since GST-free is the correct
treatment.

### Veronica Cerratti Pty Ltd — 38 transactions restored to GST-free
`data_fixes/fix_veronica_restore_gst_free_allocation.py` (snapshot:
`veronica_restore_gst_free_snapshot.json`).

Her accountant *had* allocated these as GST-free; an account re-assignment
overwrote it. Each of the 38 carried the fingerprint of an overwritten manual
decision: `is_gst_manual=True`, `creditable_percentage=0.00` (only ever zeroed
for a non-taxable treatment) and `gst_treatment='gst'` — the off-choices value
written *only* by `review_bulk_edit_transactions` from the account's tax code.
$10,514.21 of GST that should never have been booked was reversed.

**Root cause of that overwrite, now fixed:** `review_bulk_edit_transactions`
derived the tax type from the *account's* `tax_code`, overriding a GST treatment
the accountant had set by hand. Assigning GST-free medical receipts to account
630 (chart-of-accounts tax code `GST`) silently made them taxable and booked GST
on them. A manually-set treatment (`is_gst_manual=True`) now wins over the
account default; the account default still applies to transactions that have not
been manually classified. Three tests pin this.

## Still outstanding — needs a decision (not applied)

### 1. Two of Veronica's restored items are not income at all
Restoring them as GST-free leaves them in G1 (total sales) with no GST, which is
better than taxable but still overstates turnover. They most likely belong at
**BAS Excluded**:

- **$70,000.00** — `Transfer from xx3378 CommBank app To pay ATO` (an internal
  transfer to fund an ATO payment)
- **$13,042.41** — `Fast Transfer From UNYOKED HEALTH PTY LTD`
- **$950.00** — `Page8of10) Statement10 …`, a statement page header parsed as a
  transaction by the OCR

### 2. Remaining stored-data inconsistencies (other clients)
| Issue | Scope after the repairs above |
|---|---|
| Non-canonical `confirmed_tax_type` (`GST`/`INP`/`CAP`) | D.P Vaughan & D Vriend (101), plus expense-side rows on the two medical files |
| Invalid `gst_treatment='gst'` | remaining rows on D.P Vaughan and the expense side |
| Taxable txns with no GST recorded | D.P Vaughan and Veronica expense rows |

These no longer affect the BAS (the engine normalises tax codes and derives GST
by the 1/11th method) but they still misrepresent the review screens.

### 3. GST control account (3380) has drifted from the transactions
Reallocating a transaction updates the transaction but never reverses or
re-posts its trial balance entries, and the balance recalc explicitly skips 3380
(`core/views.py:5050`). Result:

| Entity | FY | TB 3380 credit vs txn GST on income | TB debit vs txn GST on expenses |
|---|---|---|---|
| Daniel Habteslassie | 2026 | +11,936.90 | +6.19 |
| Veronica Cerratti Pty Ltd | 2026 | +6,915.40 | 0.00 |
| D.P Vaughan & D Vriend | 2025 | 0.00 | −10,924.94 |

So the **balance sheet GST liability is wrong** for all three, independently of
the BAS. Fixing this needs both a code change (reallocation must re-post, or the
recalc must rebuild 3380) and a data repair — both are hard to reverse on live
client ledgers, so neither was done.

### 4. Imported trial balances are not grossed up
Only `bank_statement` aggregates get the 11/10 treatment, so a TB-import client's
G1 is the GST-exclusive balance and 1A is G1/11 rather than 10% of a grossed-up
G1. Whether that is right depends on whether the source ledger stores
GST-inclusive figures. Left unchanged (it would move every TB-import client's
BAS); pinned by a test so the current behaviour is explicit.

## Still absent from the engine (unchanged, previously documented)
No PAYG withholding (W1/W2), no PAYG instalments (T1/T7/5A/5B), no WET/LCT/fuel
tax credits. This is a GST-only worksheet, not a full BAS. G2 (exports) and G15
are still never populated — no code path assigns them.
