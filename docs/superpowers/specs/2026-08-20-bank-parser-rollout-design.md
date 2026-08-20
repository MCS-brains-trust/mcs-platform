# Bank Statement Parser Rollout — Design

**Date:** 2026-08-20
**Status:** approved design, not yet implemented
**Branch:** `feat/bank-parser-rollout`
**Prompted by:** the Veronica Cerratti July upload (see PR #60)

---

## 1. Why this exists

A CBA statement covering 1 May – 30 Jul 2026 imported 110 of its 240
transactions and reported a closing balance 8,197.45 away from the figure
printed on it. Nothing objected. The cascade began with a 0.675pt typesetting
offset that made the geometry engine reject a statement it had in fact parsed
perfectly, which dropped the upload into Vision OCR, which dated 53 June rows
to 2025 because the two pages it was given printed no year — and because only
the *dates* were wrong, every balance check still passed. The financial-year
filter then discarded those 53 rows as out of period, taking 22,399.41 of real
movement with them.

PR #60 fixed the five defects in that chain and added a hard gate at import:
nothing whose own figures contradict it can become a ReviewJob without a
recorded, reasoned override.

**The gate changed what this project is for.** Before it, a broken parser meant
silently wrong figures in the ledger. Now it means a loud refusal at the door.
So this rollout is no longer a safety project — safety is covered for all nine
banks by the gate — it is a *throughput* project: making statements import
correctly instead of needing an override.

That reframing drives the priority order in §5: sequence by how much work each
bank actually blocks, not by how fragile its parser looks.

---

## 2. What the evidence actually says

Thirteen distinct real statements, surveyed 2026-08-20. This table is the
baseline every phase is measured against.

ANZ was parked when this document was first written: its only exemplar was
missing pages, so it could never reconcile whatever the parser did. Two
complete statements arrived the same day and both reconcile to the cent
through the existing flat-text parser, which unparks ANZ without any code
change. They are not consecutive, so no boundary check applies to them.

| File | Bank | Rows | Anchors | Reconciles | Parser actually used | State |
|---|---|---|---|---|---|---|
| `cba_stmt9.pdf` | CBA | 174 | 27,440.30 → 8,826.22 | yes | geometry | healthy |
| `cba_stmt10.pdf` | CBA | 188 | 8,826.22 → 26,420.53 | yes | geometry | healthy |
| `July.pdf` | CBA | 240 | 26,420.53 → 14,001.89 | yes | geometry | fixed in PR #60 |
| `CBA1.pdf` | CBA | 108 | 16,759.60 → 6,218.20 | yes | **legacy fallback** | engine cannot detect columns |
| `CBA2.pdf` | CBA | 120 | 6,218.20 → 10,950.76 | yes | **legacy fallback** | engine cannot find closing anchor |
| `CBA_1.pdf` | *unknown* | — | 805.27 → 7,988.66 | — | none | unsupported format |
| `WBC1.pdf` | Westpac | 2 | 14,649.20 → 21,338.83 | yes | flat-text | correct (genuinely 2 rows) |
| `WBC2.pdf` | Westpac | 2 of 3 | 11,259.57 → 14,649.20 | **no** | rejected at parse | loses a wrapped row |
| `NAB1.pdf` | NAB | 413 | 19,024.84 → 13,349.18 | yes | flat-text | healthy |
| `NAB2.pdf` | NAB | 370 | 12,624.69 → 19,024.84 | yes | flat-text | healthy |
| `ANZ1.pdf` | ANZ | 6 | 11,940.79 → 545.75 | yes | flat-text | healthy |
| `ANZ2.pdf` | ANZ | 9 | 3,045.71 → 5,454.62 | yes | flat-text | healthy |
| `ING.pdf` | ING | 56 | **0 → 0** (should be 2,156.82 → 3,514.82) | no | flat-text | **gate refuses it** |

### The strongest signal available

Five pairs chain balance-to-balance across consecutive statements:

```
cba_stmt9 → cba_stmt10 → July     8,826.22    then  26,420.53
CBA1      → CBA2                  6,218.20
WBC2      → WBC1                 14,649.20
NAB2      → NAB1                 19,024.84
```

Each closing balance equals the next statement's opening balance exactly. This
matters more than single-statement reconciliation, because a parse can be
internally consistent and still wrong — the CBA engine was trusted on two
statements precisely because two independent documents cannot be fitted by
accident. **Cross-statement continuity becomes a permanent test (Phase 0).**

### Caveat on the row counts

`CBA1` and `CBA2` produced 108 and 120 rows *via the legacy flat-text parser*.
Those counts are **provisional**, not ground truth: the legacy parser may merge
or split rows differently from the geometry engine. The load-bearing acceptance
criteria are reconciliation to the cent and cross-statement continuity, not the
row count. Row counts get frozen only once the engine has produced them and a
human has agreed they are right.

---

## 3. Root causes, per bank

### 3.1 CBA — three formats, one supported

The engine handles the modern layout only. `CBA1`/`CBA2` are an **older CBA
layout**: words arrive unglued and amounts carry a `$` prefix —
`['01','Jul','2024','OPENING','BALANCE','$16,759.60','CR']` — against the
modern glued form `['01May2026OPENINGBALANCE','26,420.53','CR']`.

Two distinct defects follow:

1. **`$` prefix defeats `MOVE_RE`.** `MOVE_RE` is `^\d{1,3}(,\d{3})*\.\d{2}$`,
   so `$16,759.60` is not an amount token. On `CBA2` the CLOSING BALANCE row
   reduces to `['30','Sep','2024','CLOSING','BALANCE','CR']` with no
   recognisable figure, and the orphan-anchor recovery added in PR #60 cannot
   fire either, because it also matches on `MOVE_RE`. Same failure class as
   July, different trigger.

2. **A sparse money column is discarded.** `_money_columns` requires
   `min_count = max(2, len(xs) // 20)`. `CBA1` clusters as 3 debits at x1≈188.8
   and 102 credits at x1≈390.1, so the threshold of 5 rejects the real debit
   column, only one cluster survives, and detection returns `None`. **Any
   statement with few debits loses its debit column entirely.** `CBA2` escapes
   only because it happens to have 13 debits against a threshold of 6.

`CBA_1.pdf` is a third format — a NetBank **Transaction History** export, not a
statement. `detect_bank` returns `unknown` (no "Commonwealth Bank"/"CommBank"
substring) and its header `Date Transaction Detail Debit Credit Balance` does
not match the `cba_txn_listing` pattern either. It is also **reverse
chronological**, and spans nearly two years (11/07/2024 → 30/06/2026).

### 3.2 Westpac — wrapped descriptions lose whole transactions

`WBC2` contains three transactions; the parser returns two. Its own summary
says Total Debits $6,140.00 while the extracted debits total $2,840.00. The
missing $3,300.00 is `07/04/26 Withdrawal-Osko Payment 1144308 Mercuri &`,
whose description wraps onto a second line, putting the amount on a line the
per-line regex never matches against a date.

This is not silent: `verify_direct_parse` rejected the statement, which is why
it fell to Vision instead of importing wrong. But it makes Westpac unusable
through the direct path.

### 3.3 ING — cannot self-verify, therefore blocked

ING prints its anchors as a header/value pair on consecutive lines:

```
Opening balance  Total money in  Total money out  Closing balance
$2,156.82        $7,408.74       $-6,050.74       $3,514.82
```

The parser reads neither line, returning `opening=0, closing=0`. Under the
policy chosen for the gate — a missing anchor blocks, because on these formats
it means the parser failed rather than that the statement prints no balance —
**every ING statement now requires an override.** ING is the only outright
blocked bank, which is why it precedes Westpac in §5.

ING also uses a **single signed-amount column** (`-260.00`, `1,800.00`) plus a
per-row balance column. That is `money_model: 'signed_amount'`, which
`CBA_PROFILE` explicitly declares out of scope today. ING is therefore the
first real consumer of that model — and, helpfully, its per-row balances make
the chain check fully effective there.

### 3.4 NAB — healthy, and the only bank already column-anchored

Both statements reconcile, chain to each other, and yield 413 and 370 rows.
`_nab_column_anchors` already derives column positions from the table header
row rather than by clustering the data — the approach that would have prevented
CBA1's failure. **NAB is the pattern the other banks should converge on**, not
a bank needing repair.

### 3.5 A defect in the new gate

`_date_order_breaks` assumes statements run forward in time. `CBA_1` is
reverse-chronological, so the gate would refuse it as "dates run backwards".
This cannot bite today — that format fails `detect_bank` first — and all nine
parseable exemplars were verified ascending. It must be fixed before Phase 4
adds support for that format. The fix is to establish a document's overall
direction first and validate monotonicity against it, refusing only genuinely
*mixed* ordering.

---

## 4. Design

### 4.1 What stays

The gate, the reconciliation guardrail (`verify_direct_parse`) and the
anchor-rebasing after period filtering are already in place and bank-agnostic.
No phase below weakens them. Every phase's definition of done includes "the
statement passes `assert_importable` on its own printed figures".

### 4.2 Targeted fixes before generalisation

Phase 1 does **not** introduce a bank-profile abstraction. Two targeted fixes
(`$`-tolerant amount matching, header-anchored column detection) resolve CBA1
and CBA2, and there is no second consumer yet. Generalising now would be
designing an abstraction against one bank's requirements.

The profile abstraction is introduced in **Phase 3, driven by Westpac**, when a
second bank genuinely needs the same machinery. ING and NAB then migrate onto
it. This ordering means the abstraction is shaped by three real formats instead
of guessed at from one.

### 4.3 The shared geometry core, when it lands

Extracted from `parse_cba_geometry`, with a per-bank profile declaring only
what actually varies:

| Profile field | Varies how |
|---|---|
| `header_labels` | `Debit`/`Credit` vs `Debits`/`Credits` vs `Money out`/`Money in` |
| `anchor_keywords` | `OPENINGBALANCE` vs `Opening balance` vs `STATEMENT OPENING BALANCE` |
| `money_model` | `two_col` (CBA, Westpac, NAB) vs `signed_amount` (ING) |
| `date_form` | glued `01May` vs spaced `01 Jul 2024` vs `01/05/26`; year present or inferred |
| `currency_prefix` | `$` present or absent |
| `row_direction` | ascending, or descending for export formats |

Column positions come from the **header row** where one exists (the NAB
approach), falling back to data clustering only when it does not — reversing
today's precedence, which is what broke CBA1.

### 4.4 Non-goals

- Not touching the Claude Vision path beyond what PR #60 changed. It remains
  the fallback, and the gate now covers its output.
- Not extending the engine to banks without exemplars. Building blind is how
  a parser comes to be trusted without evidence.
- Not populating `account_number` / `period_start` / `period_end` from the
  geometry engine. It would start auto-creating `BankAccount` records, which is
  a behaviour change nobody asked for. Tracked separately.
- Not reworking row grouping generically unless Phase 1 shows it is warranted.

---

## 5. Phases

Each phase is independently shippable and ends green.

### Phase 0 — Lock in what works

A fixture-driven regression harness over all 11 statements asserting, per file:
detected bank, transaction count, both anchors, reconciliation to the cent, and
that `assert_importable` accepts it. Plus the four cross-statement continuity
chains from §2.

The fixtures are gitignored client documents, so the harness **skips with a
clear message when they are absent** and never fails CI on a machine that does
not have them. A skip is reported, not silent.

*Done when:* the harness passes on this machine, skips cleanly without
fixtures, and encodes the §2 table as the baseline.

### Phase 1 — CBA hardening *(first, per Elio)*

CBA is not *blocked* — `CBA1`/`CBA2` do import — so this leads on a different
ground than §1's rule. Two of the five CBA statements are riding the legacy
flat-text parser without anyone knowing, and CBA is by far the highest-volume
bank here. Silent reliance on the parser we least trust, on the bank used most,
outranks an outright block on a bank used rarely.

1. Tolerate a `$` prefix wherever amounts are matched, including orphan-anchor
   recovery.
2. Detect columns from the header row when present; keep clustering as
   fallback; remove the population floor that discards a sparse real column.
3. Get `CBA1` and `CBA2` onto the geometry engine, off the legacy parser.
4. Re-examine the `round(top)` row-grouping quantisation now that five CBA
   statements are available instead of two — the generic fix was deliberately
   declined in PR #60 for lack of evidence. Decide with data; record the
   decision either way.

*Done when:* all five standard CBA statements parse **via geometry**, reconcile
to the cent, chain to their neighbours, pass the gate, and no description
carries page furniture. Legacy `parse_cba_statement` remains only as a
fallback, and a test asserts geometry — not legacy — handled each one.

### Phase 2 — ING anchors *(the only fully blocked bank)*

Read the header/value anchor pair; add the `signed_amount` money model; use the
per-row balance column for chain verification.

*Done when:* `ING.pdf` yields 56 rows anchored 2,156.82 → 3,514.82,
reconciles, and imports without an override. Marked **provisional** in code and
docs: one exemplar is enough to build against and not enough to trust.

### Phase 3 — Westpac + the profile abstraction

Geometry parser for Westpac, accumulating description rows until a movement is
found, which fixes the wrapped-description loss by construction. Extract the
shared core and profiles per §4.3.

*Done when:* `WBC1` gives 2 rows and `WBC2` gives **3**, including the
3,300.00 Osko payment; both reconcile and chain to each other; the CBA and NAB
results from Phases 0–2 are unchanged.

### Phase 4 — CBA Transaction History + reverse ordering

`detect_bank` branch for the Transaction History export; a parser for its
`Date / Transaction Detail / Debit / Credit / Balance` layout; and the
`row_direction` fix so the gate stops treating a descending document as
disordered.

*Done when:* `CBA_1.pdf` parses anchored 805.27 → 7,988.66, reconciles, and
passes the gate while still refusing genuinely mixed ordering. Note this export
spans two financial years, so it will exercise the FY-straddle path heavily.

### Phase 5 — NAB and ANZ onto the shared core

No behaviour change. Migrate NAB and ANZ onto the profile abstraction and keep
`verify_nab_columns`.

*Done when:* all four NAB and ANZ statements produce byte-identical results to
Phase 0.

---

## 6. Parked banks

Gate-protected, explicitly untrusted, and **not** to be quietly assumed
working: **Bank of Melbourne**, **Macquarie**, **Bendigo**, and the CBA NetBank
**"Transaction Listing"** variant.

What unparks one: two real statements from different periods, each printing an
opening and closing balance.

**ANZ is no longer parked.** Two complete statements now reconcile through the
existing parser, so ANZ needs no repair — it joins NAB as a bank that works and
is simply waiting to migrate onto the shared core in Phase 5. This is worth
noting as the cheapest result in the whole survey: the fix was evidence, not
code.

Until then a statement from these banks either parses and reconciles by luck,
or is refused by the gate and needs a reasoned override. That is the honest
state of them, and it is visible rather than assumed.

---

## 7. Risks

| Risk | Handling |
|---|---|
| Header-anchored column detection regresses a bank that currently clusters successfully | Phase 0 harness runs first; every phase re-asserts all 11 |
| ING shipped on one exemplar | Labelled provisional in code and docs; the gate still checks every import |
| Removing the `min_count` floor admits a spurious column | Reconciliation must still pass; a wrong column makes the statement fail loudly |
| Row-grouping rework destabilises five working statements | Phase 1 decides with data and may decline again; declining is an acceptable outcome |
| Legacy parsers keep masking engine failures, as they did for CBA1/CBA2 | Phase 1 tests assert *which* parser handled each file, not just the result |

---

## 8. Open questions

1. `CBA3`–`CBA6` never arrived (`CBA1`/`CBA2` were re-sent as `CBA_2`/`CBA_3`).
   More of the **older** `$`-prefixed format would strengthen Phase 1, which is
   where the defects actually are.
2. Is `CBA_1`'s Transaction History export something the firm uses routinely,
   or was it a one-off? If routine, Phase 4 should move ahead of Phase 3.
3. ~~A replacement ANZ statement would unpark ANZ.~~ **Answered
   2026-08-20:** two complete ANZ statements supplied, both reconcile, ANZ
   unparked with no code change.
4. Bank of Melbourne, Macquarie, Bendigo and the CBA NetBank "Transaction
   Listing" still have no exemplars at all. They stay parked until two each
   arrive.
