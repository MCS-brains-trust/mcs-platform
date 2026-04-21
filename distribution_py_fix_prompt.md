# Distribution Summary: Fix PY Figures Using Prior Year TB Closing Balances

## Background

The Beneficiaries Profit Distribution Summary currently looks for `rollover` lines in the prior year FY to get PY opening balances. However, many trust entities will only have `tb_import` lines in the prior year — no rollovers. This means PY figures show as "-" even when prior year data exists.

The fix: use the prior year `tb_import` closing balances directly as PY figures, regardless of source type. The closing balance on a TB line IS the prior year position.

---

## PHASE 1 — AUDIT (read files, do not write any code yet)

In `core/fs_template_service.py`, find `_build_beneficiary_distribution_summary` (around line 3426). Report:

1. The exact function `_figures_for_year` or equivalent — how it currently fetches TB lines for the prior year and what `source` filter (if any) it applies
2. How it computes `opening` balance for the prior year — specifically whether it requires `rollover` lines or uses any source
3. How it computes `closing` balance for the prior year
4. How it fetches `profit_dist` for the prior year — specifically what it reads from `fy.prior_year.trust_workspace`
5. The exact lines where PY data is assembled and passed to the rendering functions

Do not proceed until all Phase 1 findings are reported.

---

## PHASE 2 — FIX

### Fix A — Use all TB lines for PY (not just rollover)

In `_figures_for_year` (or equivalent), when computing figures for the prior year:

- Remove any filter that restricts to `source='rollover'`
- Use ALL non-rollover TB lines (tb_import, manual_journal) for the prior year closing balance
- The opening balance for PY = the closing balance from the prior year's prior year (i.e. `fy.prior_year.prior_year`) — if that doesn't exist, default to 0
- The closing balance for PY = sum of closing_balance across all TB lines for that account in fy.prior_year, regardless of source

Specifically for each beneficiary officer, for the prior year:
```python
# PY closing = sum of closing_balance on all TB lines for this officer's accounts in fy.prior_year
py_closing = sum of (-l.closing_balance for non-rollover lines in py_fy for this officer's account codes)
# sign-flipped because equity accounts are credit-normal
```

### Fix B — PY profit distribution fallback

When `fy.prior_year.trust_workspace` is None or has no selected scenario:
- Set PY profit distribution to `Decimal('0')` for all beneficiaries
- Do not show "-" — show 0 formatted as "-" (consistent with other zero values)
- Do NOT attempt to access `trust_workspace.selected_tax_scenario` — guard with `getattr` and handle None gracefully

### Fix C — PY funds_loaned derivation

After fixing PY closing and PY profit_dist, re-derive PY funds_loaned using the same identity:
```python
py_funds_loaned = py_closing - py_opening - py_profit_dist + py_physical_dist
```

This ensures the PY reconciliation ties out even when profit distribution data is unavailable.

### Fix D — PY opening balance

For the PY column, the opening balance is what each beneficiary had at the START of the prior year (i.e. end of the year before that). 

Use: the `prior_debit - prior_credit` from the rollover lines in `fy.prior_year` if they exist, otherwise use 0.

Actually — use the closing balance from `fy.prior_year`'s own rollover lines if available:
```python
# PY opening = sum of prior_debit - prior_credit from rollover lines in py_fy for this officer
# If no rollover lines exist, PY opening = 0
```

---

## PHASE 3 — VERIFY

Run on the server:

```
source /opt/statementhub/venv/bin/activate && cd /opt/statementhub && python3 manage.py shell -c "
from core.models import FinancialYear
from core.fs_template_service import generate_financial_statements
fy = FinancialYear.objects.get(pk='dcdd1d3e-379d-42d6-a364-d2cc075f1235')
fy.generated_documents.filter(document_type='financial_statements').delete()
fy.package_assembled = False
fy.save(update_fields=['package_assembled'])
result = generate_financial_statements(fy.pk)
print('Success:', list(result.keys()))
"
```

Then check the PY figures match the Handiledger reference:

**Expected PY closing balances (from Handiledger):**
- Elio Scarton: 94,407
- Jess Scarton: 291,240
- Method Auditing Pty Ltd: (85,035)

Confirm no errors.

---

## PHASE 4 — COMMIT

```
git add -A
git commit -m "fix: use prior year tb_import closing balances for PY figures in distribution summary, handle missing trust workspace gracefully"
git push origin master
```

Report the commit hash.
