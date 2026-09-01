# BAS period coverage for journal-sourced GST — design

**Date:** 2026-09-01
**Driving client:** Elliott Jaques (`bcb8a828-2791-4788-8b17-1964dd0d1a93`), sole trader, GST registered, quarterly BAS
**Status:** design approved, ready for implementation plan
**Follows:** `2026-08-31-cashbook-gst-journals-design.md` (merged as PR #97)

## Problem

The cashbook GST work gave a journal its own GST, and the BAS engine reports it:
`calculate_gst_for_period` returns 1A 2,107.91 / 1B 418.68 for Elliott Jaques'
Q2, and the dashboard renders those figures correctly once the quarter is
selected.

Everything *around* the figures still assumes GST can only come from a bank
feed. `get_bank_coverage` answers "is the bank data complete?" by walking
confirmed `PendingTransaction` records month by month, and
`compute_period_status` reuses that single answer for a different question —
"is this period accounted for?". Those were the same question while bank
statements were the only source of GST. They are not any more.

Three consequences on a quarter that holds 2,107.91 of real GST, in increasing
order of harm:

1. **The dashboard opens on the wrong quarter.** Auto-selection
   (`views_bas.py:131`) looks for a period with status `ready`, then `partial`,
   then falls back to the first period. Elliott Jaques' Q2 computes as `empty`,
   so nothing matches and the page lands on Q1 (Jul–Sep), which is genuinely
   empty. The accountant's first impression of a completed BAS is a blank one.

2. **A false warning.** The same period reports "No bank transactions" and
   "Bank statement coverage is incomplete for this period". Both are literally
   true and both are misleading: there is no bank feed because the quarter was
   journalled, not imported.

3. **Lodgement is gated behind an override.** `bas_lodge_period`
   (`views_bas.py:263`) refuses to lodge unless coverage is `complete` or the
   accountant types an override reason. Every cashbook BAS therefore requires
   overriding a safety gate on a quarter that is actually complete. This is the
   real damage: it trains accountants to treat the override as routine, which
   erodes the control precisely for the bank-fed entities where it does real
   work.

None of this is a regression. It is a set of assumptions the cashbook work
exposed.

## Constraint: journals have no line dates

`JournalLine` carries no date — only `AdjustingJournal.journal_date`. Elliott
Jaques' JE-001 is a single 31/12/2025 date covering all of October–December.

Month-by-month coverage is therefore *not derivable* from a journal the way it
is from bank transactions. Any design that tries to report which months a
journal covers is inventing data. This rules out per-month journal coverage and
shapes everything below: a journal is evidence about a *period*, not about the
months inside it.

## Decisions

Two questions were settled before design:

**What counts as complete for a journalled period?** A posted cashbook journal
dated inside the period makes that period complete — no override needed.
Posting the journal *is* the accountant asserting the quarter is written up,
which is the same assertion that importing and confirming every bank
transaction makes.

**What about a period with both?** The journal counts only when the period has
no bank activity at all. The moment a period has any confirmed bank month,
today's month-by-month rule governs it unchanged. This is deliberately the
narrowest possible change: behaviour moves only for periods that are purely
journalled — exactly the new case — and the existing control is untouched
wherever it currently does work.

Accepted cost of the first decision: a journal covering only part of a quarter
still reads as complete, and nothing catches it. Given the constraint above,
nothing *can* catch it without line-level dates.

## Architecture

One new function, and the existing one left honest.

### `get_bank_coverage(fy, period_start, period_end)` — unchanged

Stays exactly as it is. It answers a bank question and answers it correctly;
it remains the primitive. No consumer calls it directly any more except the
new function.

### `get_period_coverage(fy, period_start, period_end)` — new

Answers "is this period accounted for?". Calls `get_bank_coverage`, then:

| Period has | Returns | Lodge gate |
|---|---|---|
| any covered bank month | the bank coverage verbatim, plus `source: "bank"` | unchanged |
| no covered bank month, and a posted CASHBOOK journal dated inside the period | `status: "complete"`, `months: []`, `missing: []`, `source: "journal"`, `journal_refs: [...]` | passes, no override |
| neither | the bank coverage verbatim (`status: "none"`), plus `source: "none"` | blocked, as today |

The "any covered bank month" test reads the `months` list `get_bank_coverage`
already returns (`any(m["covered"] for m in months)`) — no new query — and it
*is* the mixed-period rule: one bank month present and the existing logic
governs untouched.

Return shape is the existing coverage dict plus two keys, so every current
consumer keeps working on the keys it already reads:

```python
{
    "status": "complete" | "partial" | "none",
    "months": [{"month": "Oct 2025", "covered": True}, ...],
    "missing": ["Dec 2025"],
    "source": "bank" | "journal" | "none",   # new
    "journal_refs": ["JE-001"],              # new, empty unless source == "journal"
}
```

`months` is **empty** when `source == "journal"`, and that is deliberate. The
constraint above says month-level coverage is not derivable from a journal;
returning a list of months marked covered would be inventing exactly the data
we just said we do not have, and any future consumer that trusted it would be
misled. A journalled period asserts completeness at the period level and says
nothing about its months. `missing` is empty for the same reason — there is no
month we can name as missing.

This is why the template must branch on `source` *before* it looks at `months`
or `missing`: with both empty, the existing `{% if pd.coverage.missing %}` /
`{% elif pd.coverage.status == 'complete' %}` chain would fall through to a
month loop that renders nothing.

### `compute_period_status` — no change

It already maps `complete → ready`, `partial → partial`, else `empty`, and
preserves `lodged`. Switching its internal call to `get_period_coverage` is the
whole change. The dashboard's auto-selection then picks the journalled quarter
as a *consequence* of it being `ready`, rather than through a special case —
problem 1 above fixes itself.

### Call sites

Four, all switching from `get_bank_coverage` to `get_period_coverage`:

- `core/bas_utils.py:254` — inside `compute_period_status`
- `core/views_bas.py:98` — the dashboard period strip
- `core/views_bas.py:260` — `bas_lodge_period`, the lodgement gate
- `core/views_bas.py:366` — `bas_coverage_check`, the JSON endpoint

## What the accountant sees

`status: "complete"` makes the gate and the auto-selection correct, but on its
own it would have the template render bank month pills for a period with no
bank data — claiming coverage that does not exist. `source` exists so the
template can tell the truth.

In `templates/core/gst_activity_statement.html`:

- Where `coverage.source == "journal"`, replace the month pills (lines 132–145)
  with "Journalled — JE-001" naming the journals in `journal_refs`.
- Suppress the "Bank statement coverage is incomplete" warning (line 1026) when
  `source == "journal"`; it is about a bank feed the period never had.
- Leave the `pd.status` badge and colour logic alone. A journalled period reads
  `ready`, which is accurate — it is ready to lodge.

`bas_coverage_check` adds `source` and `journal_refs` to its JSON response.

## Scope

**In:** `get_period_coverage`, the four call-site switches, the two template
branches, the endpoint fields, and their tests.

**Out, deliberately:**

- Renaming `get_bank_coverage`. It is correctly named for what it does.
- Per-month journal coverage. Not derivable — see the constraint above.
- Any change to `calculate_gst_for_period` or the 1A/1B figures. They are
  already correct.
- Any change to `bas_download`. PDF and Excel export already work for a
  journalled period (verified: 15KB PDF, 6.5KB Excel).
- A journal dated *after* period end counting for that period. A quarter
  written up in a journal dated 5 January will not count for Oct–Dec. This is
  the accepted cost of the "dated inside the period" rule; revisit only if it
  bites in practice.

## Implementation note

`views_bas.py:92` calls `compute_period_status` and `:98` calls the coverage
function, once per period — so coverage is computed twice per period, and will
still be after this change. That duplication exists today and this design does
not add to it; leave it alone. If it ever matters, the fix is an optional
`coverage=` argument on `compute_period_status`, not a change to either
function's meaning.

## Judgment calls

**Only `journal_type = CASHBOOK` counts.** A cashbook journal is the one that
asserts "this is the written-up cash book for this period". A general adjusting
journal that happens to carry GST makes no such claim, and treating it as
period evidence would let a single year-end adjustment mark a quarter complete.

**The journal need not carry any GST.** An all-`N-T` cashbook quarter is a
legitimate nil BAS. Requiring a non-zero `gst_amount` would block exactly the
quarter that has nothing to report, which is the one case where the accountant
most needs the lodgement to be frictionless.

**The journal must be posted.** A draft journal is not an assertion that
anything is finished.

## Testing

New `core/tests_bas_period_coverage.py`, TDD, each test failing first:

*The new behaviour*
- A period with no bank data and a posted cashbook journal dated inside it →
  `status "complete"`, `source "journal"`, `journal_refs` naming the journal
- …and `compute_period_status` returns `ready` for it
- …and `bas_lodge_period` lodges it with no `override_reason`
- …and the dashboard auto-selects it rather than the empty first quarter

*The existing behaviour, unchanged*
- Bank months all covered → `source "bank"`, `status "complete"` (regression)
- Oct+Nov covered, Dec missing, **plus** a posted cashbook journal → still
  `partial`, still `missing: ["Dec 2025"]`, lodgement still blocked without an
  override. This is the mixed-period decision and the most important guard in
  the file.
- Nothing at all → `status "none"`, `source "none"`, lodgement blocked
- A *draft* cashbook journal → does not make the period complete
- A posted **general** journal carrying GST → does not make the period complete
- A cashbook journal dated after period end → does not count for that period
- `get_bank_coverage` itself still answers the bank question only, whatever
  journals exist

*Whole-suite guard*
- Full `core` suite, failure set compared against the pre-change baseline, not
  the count.
