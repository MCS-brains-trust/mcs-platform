# A Tier 2 spec for the bank statement to BAS flow

## Purpose

Uploading a bank statement, allocating its transactions, posting them to the trial
balance and producing the GST figures is the largest untested surface in the platform,
and the only one whose output goes to the ATO. Verified 2026-08-14:

- No Tier 1 or Tier 2 spec touches bank statement upload, allocation, posting or BAS.
- Tier 1 crawls routes for HTTP status only, and excludes mutating routes by design —
  100 of them — as explicitly "Tier 2's job".
- Tier 2's 32 tests cover roll-forward and year-end close, nothing else.

This spec closes that gap for one bank format and one entity type, in a shape that
extends to more of each.

## Why a real statement matters, and why it cannot be the fixture

The defect this area has already produced was invisible to any synthetic input.
`page.extract_text()` drops spaces where font kerning is tight, so the date `31 Oct`
arrived as `31Oct`, the CBA date regex matched zero lines, and the parser returned "No
transactions could be extracted" — bank detected, header matched, zero rows. Debit
versus credit was encoded only in horizontal position, which flat text discards. Fixed
for CBA in `b073cca` (2026-06-24) by the geometry engine in
`review/statement_geometry.py`; the root cause is documented in
`DISCOVERY_REPORT_bank_parser_regression.md`.

A fixture invented from the regex alone would never have reproduced that. A real
statement would — but Tier 2 fixtures are committed, and a client's account number,
balances and transaction history cannot go into git history.

**Decision: synthesise the fixture from the real statement's geometry.** The real PDF is
read locally to learn its measurable properties; a generator script reproduces those
properties with invented names and amounts. The real file is never committed and never
leaves the machine.

## Decisions

**The fixture is a generator script, not a checked-in binary.** `reportlab 4.4.10` is
available in the venv. The committed PDF is a build artifact of
`e2e/fixtures/statements/make_cba.py`, so what a reviewer reads is a transaction table
and layout constants rather than an opaque blob. The properties it must reproduce:

- kerning tight enough that `31 Oct` collapses to `31Oct` in non-layout extraction —
  the exact condition that produced the empty-result defect
- debit and credit column x-positions, the only encoding of the sign
- header text matching the `detect_bank` regex, so the CBA path is selected
- a running balance that reconciles, so the geometry engine's gate passes rather than
  falling through to the legacy parser

The transaction set is deliberately mixed: GST-taxable, GST-free, a debit and a credit
sharing one date, and a description long enough to wrap.

**CBA only, for now.** The geometry engine is gated on `if bank == "cba"`
(`review/pdf_parsers.py`); the other eight parsers still run flat `extract_text()` plus
per-line regex, which is where the defect class still lives. Covering them is more
valuable than covering a second entity type — but only with a real exemplar per bank.
Synthesising an ANZ or NAB layout from its regex would bake in guesses and produce a
fixture that passes because it matches its author's assumptions.

**Allocation is driven deterministically; the AI suggestion step is out.** The test
drives the real allocation UI in the browser — that UI is part of what is under test —
but chooses each account code itself rather than accepting an AI suggestion. Tier 1 already excludes
59 routes as metered — costing money per call — and AI suggestions vary between runs, so
assertions over them would have to be loose enough to be worthless. Everything
downstream, where the money errors live, is covered either way.

**One entity type, structured for more.** Company first. `entity_type` reaches the GST
path only to select the chart of accounts (`core/bas_utils.py:341-345`); the arithmetic
is driven by tax codes on transactions (`GST_FREE_CODES`, the `tax_type_map`). A second
entity type would mostly re-prove the same sums. The flow lives in a parameterised
module so a second type is a thin spec file, exactly as `roll_forward_flow.ts` is called
by four one-line specs.

**Figures are hand-computed, not baselined.** This departs from Tier 2's
`figures.baseline.json` convention and is the spec's most deliberate choice. Every BAS
label — G1, G2, G3, G10, G11, 1A, 1B, net — is worked out by hand from the fixture's
transaction table and written into the test with its arithmetic shown. A baseline blesses
whatever the code produced the first time it ran; these figures go to the ATO. The same
reasoning is set out at length in
`docs/superpowers/specs/2026-08-13-company-fs-generation-test-design.md`, where a golden
baseline would have blessed two defects that kept the statements balancing while wrong.

**Lodgement is safe to exercise.** `bas_lodge_period` (`core/views_bas.py:228`) marks
status and snapshots the GST figures. There is no ATO transmission and no external call
anywhere in that module, and `bas_unlodge_period` reverses it.

## The flow

Upload → parse → allocate → post to trial balance → GST calculation → coverage check →
lodge → unlodge.

## What is asserted

| Stage | Assertion |
|---|---|
| Parser | transaction count; dates parsed despite the kerning collapse; debits and credits on the correct side; the reconciliation gate passes rather than falling back |
| Posting | trial balance lines created with the correct sign |
| Posting | the double-post guard at `review/views.py:651` holds — a repeated confirm must not post twice |
| GST | every BAS label equals its hand-computed value |
| GST | `1A − 1B = net GST`, and posted TB lines sum to the statement's net movement |
| Coverage | lodgement blocked on incomplete bank coverage; permitted with an override reason |
| Lodgement | the snapshot freezes figures at lodge time and survives later edits |
| Unlodge | permission-gated to senior accountants |

## What this does not cover

Stated so the suite is not read as proving more than it does:

- The AI suggestion path, including `accept_all_suggestions`
- The other eight bank formats, where the original defect class still lives
- Entity types other than company
- ATO transmission, which does not exist in the code

## Operational constraint

Each Tier 2 spec file boots a Django instance and a ~471 MB database branch, and
**production shares this host** — the e2e README caps full-tier runs at `--workers=2`.
This spec should be developed and run at one worker, off-peak.

## Files

```
e2e/tier2/bank_to_bas_flow.ts            the flow, parameterised by entity type
e2e/tier2/bank_to_bas_company.spec.ts    thin caller
e2e/fixtures/statements/make_cba.py      fixture generator
e2e/fixtures/statements/cba_sample.pdf   generated, committed
```

## Verification

Complete when all of these hold:

1. `bank_to_bas_company.spec.ts` passes at `--workers=1`.
2. The existing Tier 2 suite still passes, and `known_failures.json` stays empty.
3. Tier 1's 215 tests still pass — new routes would show as a manifest diff.
4. The fixture PDF regenerates byte-for-byte from `make_cba.py`, so a reviewer can
   confirm the committed binary is exactly what the script produces. This requires
   reportlab's invariant mode — by default it embeds a `/CreationDate` and no two runs
   match. Verified 2026-08-14: `canvas.Canvas(buf, invariant=1)` with
   `rl_config.invariant = 1` produces byte-identical output across runs, plain
   `Canvas(buf)` does not.
5. No real client statement is committed, and none is left in the working tree.
