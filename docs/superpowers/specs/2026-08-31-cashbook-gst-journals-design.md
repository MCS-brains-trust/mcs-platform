# Cashbook GST journals — design

**Date:** 2026-08-31
**Driving client:** ELLIOTT JAQUES (`bcb8a828-2791-4788-8b17-1964dd0d1a93`), sole trader, GST registered, quarterly BAS
**Status:** design approved, ready for implementation plan

## Problem

An accountant working a cash-basis client journals the period's transactions
straight into the trial balance. There is no bank feed and no review data: the
journal *is* the primary transaction record. GST therefore has to be accounted
for inside the journal itself, because nothing downstream will do it.

Today it cannot be. `JournalLine` carries only
`account_code, account_name, description, debit, credit` — there is no tax code
and no GST field. The tax-code dropdown in `journal_edit.html:244` belongs to
the *create a new account* modal, not the line grid.

The live consequence, on ELLIOTT JAQUES' only financial year (a stub quarter,
1 Oct – 31 Dec 2025, `status=draft`, unlocked, zero `BASPeriod` rows). Its
trial balance is 100% `source='manual_journal'`. One posted journal, JE-001
dated 31/12/2025, "Oct-Dec 2025 Income & Expenses":

```
105   Sales                       Cr 23,187.00
1510  Accountancy                 Dr    250.00
1804  M/V car - Fuel & oil        Dr  1,990.40
1845  Protective clothing         Dr    200.00
1940  Telephone & Internet        Dr    135.00   (two lines: 85.00 + 50.00)
1809  M/V car - Other (tolls)     Dr    605.00
1946  Tools                       Dr    510.00
1800  Materials & supplies        Dr    570.00
1808  M/V car - Repairs           Dr    345.00
4080  Drawings                    Dr 18,581.60   ← balancing plug
```

No bank line, no `3380`, no tax code. A pure cash-in/cash-out cashbook journal
with drawings absorbing the residual. Three defects follow:

1. **The P&L is GST-inclusive.** Sales sits at 23,187 gross and expenses at
   4,605.40 gross. For a registered entity both belong net.
2. **The balance sheet is missing the ATO liability.** The ~1,689 owed appears
   nowhere. The bank path already gets this right — `_post_txn_to_tb`
   (`core/views.py:1070`) does the full triple entry: net to the P&L account,
   GST to `3380`, gross to bank. The journal path has no equivalent.
3. **The BAS engine silently assumes journal amounts are gross.**
   `_classify_line` grosses up only when `source == "bank_statement"`, so
   journals pass through untouched and 1/11th is taken off them. That happens
   to give the right answer for JE-001 — but only because it was keyed gross,
   and nothing records or enforces that. Keyed net, the BAS understates by 10%
   with no warning.

### Why the default matters more than the mechanism

All 58 posted journals across the 9 entities were surveyed. Year-end types
(depreciation, tax, distribution) never touch a GST-coded account, and the
general journals that do are almost all `FRE`. One is not:

**Hazaway JE-002, 30/06/2024, "migrated previous accountant profit & loss"** —
touches `1510(INP)`, `1515(INP)`, `0630(GST)`, `0610(GST)`, `0570(GST)`. Those
figures came off another accountant's financials and are *already net*. A GST
column that defaulted to the chart's tax code would strip 1/11th out of every
line of a journal like that. So GST awareness must be opt-in per journal, not
inferred from the chart.

## Decisions

| # | Decision | Rationale |
|---|---|---|
| 1 | Accountant keys **gross** plus a tax code per line; the split is materialised as **visible, editable journal lines** | The journal is the audit record. Keeps the gross figure the accountant actually saw on the invoice, and lets a partial credit be overridden in place. |
| 2 | **One 3380 pair per journal** — a single Cr "GST collected" and a single Dr "GST paid" | An 11-line cashbook journal stays readable. Per-line GST is still stored on each `JournalLine`, so drill-down and the BAS detail tabs lose nothing. |
| 3 | New **`JournalType.CASHBOOK`** gates the tax columns; every other type behaves exactly as today | Zero regression risk on the 58 existing journals, Hazaway JE-002 included. The accountant opts in by choosing the type. |
| 4 | **Full 1/11th with a hand override** per line; the untested apportionment engine is not wired in | `EntityGSTSetting` and `detect_apportionment` (`review/views_enhanced.py:930`) exist but have **zero rows in production**. Not putting a never-exercised path on the critical journal-posting flow. Apportionment can layer on later. |
| 5 | **Per-line GST is authoritative** (accounts method). 1A/1B are reported as the sum of actual GST recorded, not `G8÷11` / `G19÷11` | The ledger and the BAS then tie to the cent by construction, which is the entire payoff. Hand overrides flow through to the BAS instead of being averaged away. Both methods are ATO-acceptable. |
| 6 | The accounts-method switch applies **platform-wide** | One arithmetic rule, no entity flags, no two code paths. Fixes the latent bank-fed mismatch everywhere. Costs four cent-level moves (see Regression surface). |
| 7 | Existing JE-001 restated by a **one-off backed-up `data_fixes` script**; no general "convert to cashbook" tooling | It is the only journal in the platform that needs it, the FY is draft and unlocked, and nothing is lodged. A general converter's blast radius covers Hazaway's migration journal and every trust's opening-balance journal. |

### The rounding fork behind decision 5

```
per-line GST (1/11 each, then summed) :  418.68   → 3380 net 1,689.23 Cr
BAS worksheet arithmetic (G19 ÷ 11)   :  418.67   → BAS net 1,689.24
                                         ------
                                          0.01 out, on the very first quarter
```

Both are legitimate — the ATO permits the calculation-worksheet method and the
accounts method — but only one can be authoritative if the reconciliation is to
be provable. Per-line wins.

## Architecture

The split is materialised at **journal save**, not at post. The decisive
consequence is that **`_post_journal_to_tb` needs no change at all**: it already
aggregates lines by account code and stamps `source_journal`, so if the
journal's own rows are net plus a 3380 pair, posting copies them verbatim and
the two 3380 rows collapse into one TB line.

```
journal_edit  (journal_type = Cashbook)
   accountant types GROSS + tax code per line
        │
        ▼
   split_cashbook_journal()              ← new unit, core/gst_journal.py
        • per line: gst = override, else 1/11 if taxable, else 0
        • line.debit/credit := gross − gst
        • store tax_code + gst_amount on the line
        • regenerate the two 3380 control lines from the sums
        │
        ▼
   JournalLine rows: net P&L lines + Cr 3380 + Dr 3380
        │
        ▼
   journal_post → _post_journal_to_tb    ← UNCHANGED
        │
        ▼
   TB: net P&L lines + one aggregated 3380 line (closing −1,689.23)
        │
        ▼
   bas_utils journal branch reads net + gst_amount    ← exact, no 1/11 guessing
```

### Restated JE-001 — the target state

```
105  Sales (net)                Cr 21,079.09      (was Cr 23,187.00)
1510 Accountancy                Dr    227.27
1804 M/V car - Fuel & oil       Dr  1,809.45
1845 Protective clothing        Dr    181.82
1940 Telephone                  Dr     77.27
1940 Internet                   Dr     45.45
1809 M/V car - Other (tolls)    Dr    550.00
1946 Tools                      Dr    463.64
1800 Materials & supplies       Dr    518.18
1808 M/V car - Repairs          Dr    313.64      (expenses net total 4,186.72)
4080 Drawings                   Dr 18,581.60      (unchanged, N-T)
3380 GST collected              Cr  2,107.91      ← generated
3380 GST paid                   Dr    418.68      ← generated
                                ------------
Dr 18,581.60 + 4,186.72 + 418.68 = 23,187.00
Cr 21,079.09 + 2,107.91          = 23,187.00
3380 closing 1,689.23 Cr  ==  BAS Q2 net 1,689.23
```

## Components

### 1. Data model (`core/models.py`)

`AdjustingJournal.JournalType` gains `CASHBOOK = "cashbook", "Cashbook (Cash Basis)"`.

`JournalLine` gains three fields:

| Field | Type | Purpose |
|---|---|---|
| `tax_code` | `CharField(max_length=10, blank=True)` | MYOB-style code, same vocabulary as `ChartOfAccount.tax_code` |
| `gst_amount` | `Decimal(15,2), default=0` | GST on this line; a hand override lands here |
| `is_gst_control` | `Boolean, default=False` | Marks the two generated 3380 lines |

`is_gst_control` rather than matching `account_code == '3380'`: this repo
already learned that lesson with `is_trust_distribution` — a structural flag
beats matching on a code or a description. An accountant can legitimately post
their own 3380 line (the quarterly ATO payment), and regenerating the split
must not wipe it.

The migration is purely additive with defaults that mean "behave as today":
every existing line gets `tax_code=''`, `gst_amount=0`, `is_gst_control=False`.

**Gross is not stored.** It is reconstructed as `(debit or credit) + gst_amount`.
That makes the split idempotent for free — re-splitting reconstructs 1,990.40
from `1,809.45 + 180.95`, recomputes the same 180.95, and lands in the same
place. A stored `gross_amount` field would be a fourth value that can drift out
of agreement with the other three.

### 2. Split engine (`core/gst_journal.py`, new)

Three pure functions, no views and no template logic, so the unit is testable
on its own:

- **`resolve_line_tax_code(entity, account_code)`** — `EntityChartOfAccount` →
  `ChartOfAccount` → `''`, normalised through the existing
  `bas_utils.normalise_tax_treatment`. That normalisation is load-bearing:
  account `1946 Tools` carries `'inp'` **in lowercase** in the live chart, and a
  naive `tax_code in TAXABLE_CODES` test would silently treat Tools as GST-free.
- **`line_gst(gross, tax_code, override=None)`** — the override if supplied,
  else `gross/11` at `ROUND_HALF_UP` when `tax_code in TAXABLE_CODES`, else nil.
  Non-creditable GST from an override stays in the expense automatically,
  because net is always `gross − gst`.
- **`split_cashbook_journal(journal)`** — runs only for `CASHBOOK`, inside
  `transaction.atomic()`, deletes and regenerates the `is_gst_control` lines,
  and asserts the journal still balances before returning. The control lines
  are appended last, taking the highest `line_number`s, so the accountant's
  own line order is never disturbed.

**Override precedence, stated explicitly** (there is no fourth
`gst_overridden` flag):

- The server trusts a `gst_amount` the form supplies for a line, and computes
  the 1/11th default only when the field is absent or blank.
- The grid recomputes GST client-side whenever gross or tax code changes, which
  is what clears a stale override.
- Consequently, re-running `split_cashbook_journal` over already-stored rows is
  a no-op, and later editing the *chart's* tax code does not silently restate a
  figure the accountant has already accepted. That is the intended behaviour:
  the accountant's number stands until they change it.

GST from credit lines accumulates into one Cr 3380 line; GST from debit lines
into one Dr 3380 line. Maximum two generated lines per journal regardless of
line count.

The balance assertion is cheap and worth keeping: every line satisfies
`net + gst = gross`, and the control lines carry the GST sums on the matching
side, so if the gross journal balanced the split journal balances identically.

### 3. Journal UI (`core/forms.py`, `templates/core/journal_edit.html`)

- `journal_type` selector gains Cashbook.
- When Cashbook is selected, the line grid shows **Tax code** and **GST**
  columns and the amount column is labelled **Gross**. Tax code defaults from
  the chart on account selection; GST computes live at 1/11 and is editable.
- Generated `is_gst_control` lines render in the grid, visually distinguished
  and not directly editable (they are derived).
- Every other journal type renders exactly the grid it renders today — no tax
  column at all.

### 4. BAS engine (`core/bas_utils.py`)

Four changes, three backward-compatible by construction:

1. **Let the line's own tax code win.** The journal branch currently calls
   `_resolve_section_and_tax(jl.account_code, coa_lookup, entity_coa_lookup, "")`.
   That hardcoded `""` means a journal line can never override the chart.
   Change to `jl.tax_code`. Existing lines carry `''` so they still fall through
   to the chart; and it is what makes an explicit `N-T` on Drawings stick.
2. **New `_jl_gross_and_gst(jl, tax_code)`**, mirroring the contract of the
   existing `_txn_gross_and_gst`:
   ```
   gross = max(jl.debit, jl.credit) + jl.gst_amount
   gst   = jl.gst_amount  or  (gross/11 if tax_code in TAXABLE_CODES else 0)
   ```
   For every existing journal line `gst_amount` is 0, so `gross` is unchanged
   and `gst` falls back to 1/11 — today's behaviour, byte for byte. Same
   "stored wins, else 1/11th" rule the bank path has used since the
   91-transaction fix.
3. **No exclusion code needed for the 3380 lines** — `_resolve_section_and_tax`
   already returns `gst_clearing` for `3380/9100/9110` and the branch does
   `if exclude_reason: continue`. Unchanged, but it gets a test, because it is
   the only thing standing between us and double-counting every cent of GST.
4. **`_build_bas_result` reports 1A/1B from summed actual GST** rather than
   `G8÷11` and `G19÷11`. G1 and G11 stay gross — they are turnover and
   purchases, unaffected.

   `G9` and `G20` move with 1A and 1B rather than keeping the worksheet
   division. Leaving them as `G8÷11` / `G19÷11` would put `G20 = 418.67`
   directly above `1B = 418.68` on the same screen — a visible 1c contradiction
   the accountant would have to explain. Since decision 5 adopts the accounts
   method, the whole worksheet reports on it consistently, and the BAS view
   carries a note saying so.

#### Regression surface (measured against the live file)

Only change 4 has blast radius. Every entity, year and quarter was computed
both ways. Four periods move:

| Entity | Period | 1A now → accounts | 1B now → accounts | Net delta |
|---|---|---|---|---|
| D.P Vaughan & D Vriend | 2026 Q4 | 9,251.13 → 9,251.08 | 5,292.26 → 5,292.25 | **−0.04** |
| ELLIOTT JAQUES | 2025 Q2 | 2,107.91 → 2,107.91 | 418.67 → **418.68** | −0.01 |
| Hazaway Operations | 2024 Q4 | 65,698.55 → same | 35,920.00 → 35,919.98 | +0.02 |
| Veronica Cerratti | 2026 Q4 | 0 → 0.00 | 3,970.02 → 3,970.00 | +0.02 |

Bank-fed entities already store per-transaction GST that `_txn_gross_and_gst`
respects, so this mismatch exists today; the switch surfaces it rather than
creating it. All four are cent-level. **D.P Vaughan 2026 Q4 is the in-flight
June 2026 BAS exercise** — the 4c move should be re-checked there before
lodgement.

### 5. Trial balance, financial statements, reconciliation

**Trial balance: no code change.** `_post_journal_to_tb` aggregates the pair
into one 3380 line at `Dr 418.68 / Cr 2,107.91`, `closing_balance = −1,689.23`,
with `source_journal` set for clean reversal.

**Balance sheet: a gap, not a data-loss one.** A fresh 3380 line gets
`mapped_line_item=None`, and `docgen` falls back to keyword/code-range
classification for unmapped accounts — so the liability does appear under
current liabilities, just not badged as the standard `BS-CL-006 – GST payable`
row. Six of the nine entities already have a `ClientAccountMapping` for
3380 → `BS-CL-006`; **D.P Vaughan and Veronica Cerratti each have a 3380 TB
line sitting unmapped today**, and ELLIOTT JAQUES has neither mapping nor line.
Fix: seed the CAM when a cashbook journal first creates 3380. Clears the two
existing unmapped entities as a side effect.

**Eva `gst_reconciliation` carries a real pre-existing bug.** The check finds
GST accounts by name keyword, then buckets them: `total_gst_collected` requires
the name to contain "gst collected"/"gst on sales"/"output tax", and
`total_input_credits` requires "gst paid"/"gst on purchases"/"input tax"
(`core/eva_engine.py:982`). The TB line is named **"GST payable control
account"** — it matches the outer `gst` filter so it gets listed, but matches
*neither* bucket. The check therefore reports `Total GST Collected: $0.00` and
`Total Input Tax Credits: $0.00` while a real balance sits in the account. This
already misreports for the six entities holding a 3380 balance. Fix: bucket the
control account off its `effective_dr`/`effective_cr` columns, not its name.

With that fixed, the check can assert what this feature exists for:

> `3380` closing balance == summed BAS net for the year, less ATO payments
> posted to `3380`

For ELLIOTT JAQUES Q2 that is `1,689.23 == 1,689.23`. It stays inside the
existing `gst_reconciliation` ADVISORY finding as a computed assertion rather
than becoming a new check.

## Migration

`data_fixes` script, following the convention already in the tree:

1. Back up JE-001 and its 10 TB lines to
   `data_fixes/elliott_jaques_je001_pre_gst_split_<ts>.json`
2. `journal_type: general → cashbook`
3. Per line, set `tax_code` from the chart (`105` → GST, the nine expense lines
   → INP, `4080` → N-T) and `gst_amount` at 1/11
4. Rewrite `debit`/`credit` to net; generate the two 3380 control lines
5. **Unpost and repost through `journal_post`** rather than patching TB rows —
   reuses the `source_journal` reversal and `_post_journal_to_tb`, so the TB is
   rebuilt by the same path a new journal would take
6. Assert: journal balances at 23,187.00 · 3380 closing = −1,689.23 · Q2
   1A = 2,107.91, 1B = 418.68, net = 1,689.23

## Testing

Tests are written before the split engine exists. They need the sqlite
override to run at all, and land on top of the known pre-existing failure
baseline.

`core/tests_cashbook_gst_split.py`
- gross cashbook journal → net lines plus exactly two control lines, still balanced
- idempotency: splitting twice equals splitting once (the reconstructed-gross property)
- a hand override: net absorbs the non-creditable GST, and a re-split preserves the override
- `N-T`, `FRE` and blank lines pass through untouched
- **lowercase `'inp'` on account 1946 Tools is treated as taxable** — the live-chart trap
- an accountant's own 3380 line (`is_gst_control=False`) survives a re-split
- general / depreciation / tax / distribution journals get no tax column and no
  split — a Hazaway JE-002-shaped fixture comes out identical

`core/tests_bas_cashbook_journal.py`
- ELLIOTT JAQUES Q2 fixture → G1 23,187.00 · G11 4,605.40 · 1A 2,107.91 ·
  1B 418.68 · net 1,689.23
- the two 3380 journal lines contribute to no G-label
- a legacy gross journal (`gst_amount=0`, `tax_code=''`) reports exactly as today

## Out of scope

- Wiring `detect_apportionment` / `EntityGSTSetting` into cashbook journals
  (decision 4) — revisit once those settings have production rows.
- A general "convert an existing journal to cashbook" action (decision 7).
- Cashbook journals that include a bank/cash line and reconcile to a statement.
  ELLIOTT JAQUES uses drawings as the balancing plug and has no bank feed; a
  bank-line variant is a separate exercise.
- The pre-existing inconsistency that a *general* journal's GST is still
  derived as 1/11th of a gross amount using the chart's tax code. Preserved
  deliberately so Hazaway's BAS does not move.
- GST on the ATO payment itself (Dr 3380 / Cr bank). Already expressible as an
  ordinary journal line; the reconciliation accounts for it.
