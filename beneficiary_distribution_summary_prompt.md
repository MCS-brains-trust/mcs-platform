# Beneficiaries Profit Distribution Summary — Replace Current Distribution Document

## Overview

Replace the current simplified Distribution Summary PDF with a full Handiledger-style **Beneficiaries Profit Distribution Summary** that shows:

- **Page 1**: Summary of each beneficiary's share of profit for the year (CY and PY)
- **Page 2**: Full loan account reconciliation per beneficiary (opening balance, movements, closing balance) tying directly to the Balance Sheet

This document replaces `_build_distribution_docx` / the reportlab DISTRIBUTION builder in `core/fs_template_service.py`.

---

## PHASE 1 — AUDIT (read files, do not write any code yet)

Report the following with exact file paths, function names, and line numbers:

1. In `core/fs_template_service.py`, find the current DISTRIBUTION document builder. Report:
   - The exact function name and line number
   - Whether it uses reportlab or python-docx
   - What data it currently uses (context variables, model queries)

2. Find `_net_beneficiary_accounts` — report what it returns and what data is available per beneficiary after netting (cy_amount, py_amount, officer name)

3. Find where distribution scenario data is loaded — specifically where profit share per beneficiary is calculated (which officer gets what percentage of net profit). Report the model names and fields used.

4. In `core/models.py`, find `ClientAccountMapping` — confirm `beneficiary_officer` FK is available and report the related `EntityOfficer` field for display name.

5. Find the `TrialBalanceLine` model — confirm `source` field and report how rollover lines store opening balances (`prior_debit`, `prior_credit`) vs tb_import lines.

Do not proceed until all Phase 1 findings are reported.

---

## PHASE 2 — BUILD

Replace the DISTRIBUTION document builder with a new reportlab-based `_build_beneficiary_distribution_summary` function.

### Data assembly

Before building the PDF, assemble data per beneficiary using this logic:

```python
# For each beneficiary officer assigned in ClientAccountMapping:
# 1. Opening balance (PY closing) — from rollover lines tagged _py
# 2. Movements by type:
#    - "Funds loaned to trust" — sum of non-rollover lines for 4004.x accounts
#    - "Profit distribution for year" — from the distribution scenario (TaxPlanningScenario or OfficerDistributionHistory)
#    - "Physical distribution" — sum of non-rollover lines for 4053.x accounts
# 3. Closing balance — cy_amount from _net_beneficiary_accounts (already computed)
# 4. Profit share — from the selected distribution scenario for this FY
```

### Page 1 — Profit Share Summary

A two-column comparative table (2024 | 2023):

```
SCARTON FAMILY TRUST
ABN XX XXX XXX XXX
Beneficiaries Profit Distribution Summary
For the year ended 30 June 2024

                              2024 $    2023 $
Beneficiaries Share of Profit
- [Beneficiary 1 Name]          X,XXX     X,XXX
- [Beneficiary 2 Name]          X,XXX     X,XXX
- [Beneficiary 3 Name]          X,XXX     X,XXX
Total Profit                    X,XXX   XXX,XXX
```

Source for profit share:
- CY: from the posted distribution scenario (sum of distributions per officer)
- PY: from `OfficerDistributionHistory` for the prior year, or from prior year rollover data if available

### Page 2 — Loan Account Reconciliation per Beneficiary

For each beneficiary, a reconciliation table:

```
[Beneficiary Name]
                                    2024 $    2023 $
Opening balance - Beneficiary       X,XXX     X,XXX
Funds loaned to trust               X,XXX     X,XXX
Profit distribution for year        X,XXX     X,XXX
                                  -------   -------
                                    X,XXX     X,XXX
Less:
Physical distribution               X,XXX     X,XXX
                                  -------   -------
Closing balance                     X,XXX     X,XXX
```

Only show "Less: Physical distribution" row if physical distribution amount is non-zero.

After all beneficiaries:
```
Total of beneficiary loans          X,XXX     X,XXX
Total Beneficiary Funds             X,XXX     X,XXX
```

### Formatting

- Font: Arial throughout (matching platform standard)
- Page size: A4 portrait
- Header: entity name bold, ABN, document title, year ended — repeating on both pages
- Footer: standard unaudited disclaimer, centred, italic 9pt, top border rule
- Negative amounts: shown in parentheses e.g. (85,035)
- Zero amounts: shown as "—" (em dash)
- Whole dollars only (no decimal places)
- Subtotal lines: single underline above
- Total lines: bold

### Integration

Replace the existing DISTRIBUTION builder call in `generate_financial_statements` with the new function. The document type key remains `'DISTRIBUTION'` so the package assembly order is unchanged.

The new function signature:
```python
def _build_beneficiary_distribution_summary(context) -> BytesIO | None:
```

Returns `None` if no beneficiary mappings exist for the entity (falls back to no distribution document).

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

Confirm DISTRIBUTION is in the result keys and no errors. Then regenerate the package from the UI and confirm the Distribution document renders correctly.

---

## PHASE 4 — COMMIT

```
git add -A
git commit -m "feat: replace simplified Distribution Summary with full Handiledger-style Beneficiaries Profit Distribution Summary"
git push origin master
```

Report the commit hash.
