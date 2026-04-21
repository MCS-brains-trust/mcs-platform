# Distribution Summary: Compare Handiledger vs StatementHub Output

## Step 1 — Read both reference documents

Read the following two files from `C:\Users\Elio\mcs-platform\handiledger_reference\`:

1. `Handiledger distribution.pdf` (or similar filename — use `dir` to confirm exact name)
2. `Statement hub distribution.pdf` (or similar filename — use `dir` to confirm exact name)

Run in PowerShell to confirm exact filenames:
```
dir "C:\Users\Elio\mcs-platform\handiledger_reference\"
```

Extract and report the full text content of both documents verbatim — every line, every figure, every label.

---

## Step 2 — Side-by-side comparison

Produce a detailed comparison table covering:

### Page 1 (Profit Share Summary)

| Element | Handiledger | StatementHub | Match? |
|---------|------------|--------------|--------|
| Document title | | | |
| Entity name | | | |
| ABN | | | |
| Date line | | | |
| Column headers | | | |
| Each beneficiary row (name + CY + PY) | | | |
| Total Profit row | | | |
| Formatting (font, layout, spacing) | | | |

### Page 2 (Loan Account Reconciliation)

| Element | Handiledger | StatementHub | Match? |
|---------|------------|--------------|--------|
| Each beneficiary block heading | | | |
| Opening balance label + figures | | | |
| Funds loaned to trust label + figures | | | |
| Profit distribution label + figures | | | |
| Subtotal figures | | | |
| Less: Physical distribution (if present) | | | |
| Closing balance figures | | | |
| Total of beneficiary loans | | | |
| Total Beneficiary Funds | | | |
| Order of beneficiaries | | | |
| Negative number presentation | | | |
| Zero presentation | | | |

---

## Step 3 — Identify all differences

List every difference found. For each:
- What it is
- What Handiledger shows
- What StatementHub shows
- Severity: Layout / Figure / Label / Missing

---

## Step 4 — Fix all differences

For each difference identified in Step 3, apply the fix directly in `core/fs_template_service.py` in the `_build_beneficiary_distribution_summary` function.

Fixes must include:
- Any incorrect figures (check the data assembly logic)
- Any incorrect labels
- Any missing rows or sections
- Any formatting differences (spacing, underlines, bold, alignment)
- Correct order of beneficiaries (match Handiledger order)
- Correct handling of negative amounts and zeros

After each fix, briefly note what was changed and why.

---

## Step 5 — Verify on server

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

Confirm no errors.

---

## Step 6 — Commit

```
git add -A
git commit -m "fix: align Beneficiaries Distribution Summary with Handiledger reference format and figures"
git push origin master
```

Report the commit hash and a summary of all changes made.
