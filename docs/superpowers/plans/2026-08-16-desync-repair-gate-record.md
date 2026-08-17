# Repair Gate record — BAS-to-trial-balance desync fixes

> **This file holds live client names and balances, and is committed deliberately.** Elio's
> call, 2026-08-17: the sensitivity is not a concern, and a record that Tasks 8 and 9 are
> formally blocked on should not live only in a temporary worktree's scratch directory. Note
> what that means and does not change: git history is permanent, and merging to `main`
> auto-deploys, so this content has travelled off the host and cannot be quietly withdrawn.
>
> Raw audit output committed alongside it as `2026-08-16-desync-audit-baseline.txt`.

**Gate defined in:** `docs/superpowers/plans/2026-08-16-bas-tb-desync-fixes.md`, section
"Repair Gate — not a code task".
**Purpose:** Tasks 8 and 9 wire the trial-balance rebuild into every live correction path.
From that moment it runs on every edit of every book. This gate exists because two accounts
cannot be rebuilt safely until a human decides what they should read.
**Blocks:** Tasks 8 and 9 must not merge until this record is signed off.

## Gate progress

| Step | State |
|---|---|
| 1. Run the audit, hand Elio the ENTANGLED section | **Done** 2026-08-17 |
| 2. Elio decides, per account, journal vs bank posting | Open |
| 3. Apply by hand, backup to `data_fixes/` first | Open |
| 4. Re-run the audit; ENTANGLED must be empty | Open |
| 5. Elio confirms in writing that both entities read correctly | Open |

---

## Step 1 evidence — the audit as run

Command, run against production from the worktree on 2026-08-17. Read-only: the command
touches `Entity`, `FinancialYear` and `TrialBalanceLine` by query only, and `_bank_tb_totals`
in `core/views.py` contains no `.save(` / `.create(` / `.update(` / `.delete(` / `bulk_` /
`get_or_create` call. Its own docstring says "Writes nothing."

```bash
cd /opt/statementhub/.claude/worktrees/bas-tb-desync
python3 manage.py audit_bank_tb_desync \
  > .superpowers/sdd/2026-08-16-bas-tb-desync-fixes/production-audit-baseline.txt 2>&1 || true
```

`|| true` is required: the command calls `sys.exit(1)` whenever it finds anything, so a
non-zero exit is the expected result, not a failure.

Raw output: `production-audit-baseline.txt` (34 lines, same directory).
Result line: `2 entangled account(s), 5 variance(s). Nothing was written.`

### ENTANGLED — the gate's actual subject

```
Daniel Habteslassie       [d82ed91d-63a3-459e-a03c-b7a2ac755d07] FY2026 account 4080
  bank postings have no bank_statement row; rows present are manual_journal

Veronica Cerratti Pty Ltd [e0833e29-665b-49ea-914c-3632bd848524] FY2026 account 3565
  bank postings have no bank_statement row; rows present are manual_journal
```

Exactly the two accounts the plan predicted, and no others. That is a meaningful negative
result: the ENTANGLED section is sourced from the deliberately **broad** `unbacked` map — every
account code with posted transactions and no `bank_statement` row, whatever its other rows'
source — not the narrow `entangled` map the rebuild uses to decide whether to decline. It cast
a wide net across every entity and every postable year and caught only these two.

Volumes from the plan's own table: Veronica 3565 holds 72 posted transactions / $419,356.03;
Habteslassie 4080 holds 25 / $237,464.00. In both cases the bank postings have been
accumulating **inside a `manual_journal` row**, because `_get_or_create_tb_line` falls through
to `qs.first()` when an account has no non-adjustment row. The rebuild refuses to touch
`manual_journal`, so wiring it without repairing these would leave a second row holding the
same money — roughly doubling both accounts.

### VARIANCE — five, reported for context, not part of the gate's blocking condition

| Entity | FY | Acct | Transactions say | Trial balance holds | Gap |
|---|---|---|---|---|---|
| Veronica Cerratti | 2026 | 3380 | Dr 12,456.33 / Cr 4.00 | Dr 12,456.33 / Cr 17,433.61 | Cr **+17,429.61** |
| Veronica Cerratti | 2026 | 630 | Dr 0 / Cr 481,561.21 | Dr 0.00 / Cr 464,131.60 | Cr **−17,429.61** |
| Habteslassie | 2026 | 3380 | Dr 6,540.73 / Cr 88.00 | Dr 6,546.92 / Cr 12,024.90 | Cr **+11,936.90**, Dr +6.19 |
| Habteslassie | 2026 | 1801 | Dr 374.96 / Cr 0 | Dr 436.90 / Cr 0.00 | Dr **+61.94** |
| D.P Vaughan & D Vriend | 2025 | 3380 | Dr 2,838.76 / Cr 20,799.76 | Dr 6,320.76 / Cr 20,799.76 | Dr **+3,482.00** |

Three things these numbers say:

1. **Veronica's two lines are the same money.** `464,131.60 + 17,429.61 = 481,561.21`, exactly.
   The trial balance still holds an income-vs-GST split the transactions no longer support. In
   financial-statement terms: income understated $17,429.61, GST payable overstated
   $17,429.61, so **net profit understated by $17,429.61**.
2. ~~**Habteslassie's income counterpart is missing from the table because it is 4080**~~ —
   **WRONG, corrected 2026-08-17 by the probe below.** Account 4080 is *Drawings*, and all 25
   posted transactions on it carry `gst 0.00` and a tax type of `N-T` or blank. They contribute
   nothing to 3380, so 4080 cannot be the counterpart to the GST overstatement. It is true that
   the audit skips entangled codes for variance comparison
   (`core/management/commands/audit_bank_tb_desync.py:102-103`), but that is not what is hiding
   the partner here. **The $11,936.90 GST overstatement on Habteslassie 3380 has no identified
   counterpart and no explanation.** The 42 income transactions the July script rewrote sit on
   some other account, which this audit did not flag — so nothing yet accounts for that figure.
3. **Habteslassie 1801 + 3380 on the debit side tell a single-transaction story.**
   `61.94 + 6.19 = 68.13`, and `68.13 / 11 = 6.19`. Consistent with one $68.13 GST-inclusive
   expense the trial balance still holds and the transactions no longer allocate to 1801.

---

## Causation — what is verified, and what does not reconcile

**Verified.** The July 2026 hand-fixes are a contributor by construction. Both
`data_fixes/fix_veronica_restore_gst_free_allocation.py` and
`data_fixes/fix_habteslassie_gst_free_income.py` rewrite `confirmed_tax_type` to
`GST Free Income` and persist with `update_fields`. **No script in `data_fixes/` mentions
`TrialBalanceLine` at all** (`grep -ln TrialBalanceLine data_fixes/*.py` returns nothing).
They corrected the transactions and left the ledger untouched — precisely the defect's shape.

**Does not reconcile, and should not be tidied away.** The amounts do not match the gaps:

| Snapshot | Rows | GST in snapshot | Gap in the ledger |
|---|---|---|---|
| `veronica_restore_gst_free_snapshot.json` | 38 | $10,514.21 | $17,429.61 |
| `habteslassie_gst_free_income_snapshot.json` | 42 | **$0.00** | $11,936.90 |

So those scripts explain part of Veronica's divergence and **none** of Habteslassie's — the 42
rows in his snapshot carried `confirmed_tax_type='GST'` (a chart-of-accounts code) with the GST
amount left at zero, per that script's own docstring. The remainder accumulated from ordinary
corrections made through the UI, which is the defect Tasks 8 and 9 fix. In other words the
divergence in these books is larger than any single known hand-fix accounts for.

**D.P Vaughan & D Vriend has no fix script and no matching counterpart line.** Different cause,
not investigated.

## Limitation of this audit — 18 years were not examined

Skipped as `finalised`, and therefore outside the entity's postable year set, so no transaction
can resolve back to them and the comparison would be meaningless:

- Berwick Mechanical Services — 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024
- Hazaway Operations Pty Ltd — 2024, 2025
- Makhmalbaf Pty Ltd — 2023, 2024, 2025
- E & J Chiaravalle Family Trust — 2025
- The Chiaravalle Family Trust — 2025
- Huxley Constructions Pty Ltd — 2025
- Vincent Family Trust — 2023, 2024

Defensible — finalised years should not be receiving corrections — but it is **not** evidence
they are clean. A desync inside a finalised year is invisible to this command.

---

## Step 2 evidence — the per-account decomposition

Produced 2026-08-17 by `probe_entangled_accounts.py` (read-only, run via
`python3 manage.py shell <` from the worktree). It takes the bank-posting figure from
`_bank_tb_totals` — the same function the rebuild uses — and subtracts it from what the rows
actually hold, **per side**. Netting the two sides hides the shape of the problem; the probe's
first version made exactly that mistake and has been fixed.

### Veronica Cerratti 3565 "Loan - Director" — decided by arithmetic

Three `manual_journal` / `is_adjustment=True` rows:

| Row | Debit | Credit |
|---|---|---|
| `1052b2f2` | 62,500.00 | 23,897.37 |
| `522b7ebc` | 164,680.00 | 0.00 |
| `b72bcca5` | 112,176.03 | 103,897.37 |
| **Total** | **339,356.03** | **127,794.74** |

Bank postings say Dr 339,356.03 / Cr 80,000.00 (72 transactions, gross 419,356.03, which splits
`339,356.03 + 80,000.00` — the 80,000 being the single inflow, "Transfer from xx3378 … Car" on
2025-11-24).

- **Debit side reconciles to the cent.** `62,500.00 + 164,680.00 + 112,176.03 = 339,356.03`
  exactly. Every debit on this account, across all three rows, is accumulated bank posting.
  Nothing to decide.
- **Credit side is out by 47,794.74**, and that decomposes exactly: `23,897.37 × 2 =
  47,794.74`. Rows `1052b2f2` and `b72bcca5` each carry a 23,897.37 credit, and `b72bcca5`
  additionally carries the 80,000 bank inflow (`80,000.00 + 23,897.37 = 103,897.37`).

So the split is fully determined: **Dr 339,356.03 / Cr 80,000.00 is bank money; Cr 47,794.74 is
genuine journal.** No apportionment judgement is required.

Two things to confirm rather than assume: that the same 23,897.37 credit appearing in two
separate adjusting journals is intentional and not a duplicated entry; and that a journal with
47,794.74 of credits and no genuine debits on this account is the expected shape.

### Habteslassie 4080 "Drawings" — needs a decision, and holds a probable double-post

One `manual_journal` / `is_adjustment=True` row: Dr 246,536.00 / Cr 9,445.36.
Bank postings say Dr 237,464.00 / Cr 0.00 (25 transactions, all `gst 0.00`, all `N-T` or blank).

| Side | Row | Bank postings | Unexplained |
|---|---|---|---|
| Debit | 246,536.00 | 237,464.00 | **9,072.00** |
| Credit | 9,445.36 | 0.00 | **9,445.36** |
| Net | 237,090.64 | 237,464.00 | −373.36 |

The net of −373.36 is the trap: it looks negligible, and it is two unrelated discrepancies of
about $9,000 each cancelling out.

- **Debit excess is exactly 9,072.00 — which is exactly one of the 25 transactions**
  ("Transfer to xx2329 CommBank app Tax April 26", 2026-05-02). `237,464.00 + 9,072.00 =
  246,536.00`. The leading explanation is that this transaction posted **twice** into the row:
  once historically, and once again in the set the aggregation now counts. It cannot be
  distinguished from a coincidental 9,072.00 journal debit without checking that entry's
  history, so treat it as a lead, not a finding.
- **Credit 9,445.36 is entirely unexplained by bank postings.** No posted transaction on this
  account is an inflow, so this is journal in origin — most likely a year-end drawings
  adjustment, but its provenance needs confirming.

Note for the repair: this row is `Drawings` on a sole trader, and its transactions are all
`N-T` transfers to what look like personal and tax-savings accounts.

## Step 2 — decisions required (to fill in)

The audit cannot decide any of this, and deliberately does not try: it detects that two records
disagree, not which one is right. Its docstring is explicit that no automated sweep can tell a
defect-induced variance from a deliberate accountant correction.

### The two entangled accounts

| Account | Question | Decision | Date |
|---|---|---|---|
| Veronica Cerratti 3565 | **Answered by arithmetic** — Dr 339,356.03 / Cr 80,000.00 is bank, Cr 47,794.74 is journal. Only needs confirming, plus: is the doubled 23,897.37 credit intentional? | | |
| Habteslassie 4080 | Is the 9,072.00 debit excess a double-posted transaction or a genuine journal debit? And what is the 9,445.36 credit? | | |

### The five variances

| Line | Question | Decision | Date |
|---|---|---|---|
| Veronica 3380 / 630 | Should the FY2026 income be GST-free? If yes the ledger is wrong by $17,429.61; if no, the transactions are. | | |
| Habteslassie 3380 (Cr 11,936.90) | Same question for his income, whose counterpart sits in entangled 4080. | | |
| Habteslassie 1801 / 3380 (Dr 61.94 / 6.19) | Which $68.13 item is this, and where does it belong? | | |
| D.P Vaughan & D Vriend 3380 (Dr 3,482.00) | Cause unknown — needs its own look before deciding. | | |

## Step 3 — applying the repair

Requirements carried over from the plan, not to be shortcut:

1. Write a backup of the affected rows to `data_fixes/` **before** changing anything, following
   the existing pattern in that directory (a timestamped snapshot JSON plus a revert script —
   `revert_from_snapshot.py` is already there).
2. Apply the decision by hand. Note the existing scripts' precedent and its flaw: they wrote
   only `PendingTransaction` fields. A repair here has to move `TrialBalanceLine` rows too, or
   it recreates the same divergence.
3. Do it before Tasks 8-9 merge, not after.

## Step 4 — re-run the audit

Same command as Step 1. **The ENTANGLED section must come back empty.** The VARIANCE section
may legitimately still hold entries if a decision was "the ledger is right, leave it" — record
which, and why, here.

## Step 5 — sign-off

Tasks 8 and 9 may not merge until this is filled in.

- [ ] Veronica Cerratti 3565 reads correctly — confirmed by: ______________ date: __________
- [ ] Habteslassie 4080 reads correctly — confirmed by: ______________ date: __________
- [ ] Re-run audit shows ENTANGLED empty — output saved as: ______________________________
- [ ] Remaining variances accepted, with reasons recorded above
