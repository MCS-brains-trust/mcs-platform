# Financial Statements: Move Notes Before Depreciation Schedule

## PHASE 1 — AUDIT (read files, do not write any code yet)

In `core/fs_template_service.py`, find the function that assembles the final package order for financial statements. Report:

1. The exact function name and line number where document types are ordered/assembled into the final PDF package
2. The current order of document types in the assembly sequence — specifically where `NOTES` and `DEPRECIATION_REPORT` appear relative to each other
3. Whether this ordering is controlled by a list, tuple, or dict — report the exact structure and all values in order

Do not proceed until all findings are reported.

---

## PHASE 2 — FIX

In the document assembly sequence, move `NOTES` to appear immediately before `DEPRECIATION_REPORT`.

Do not change the order of any other documents. Apply across all entity types (company, trust, partnership, sole_trader).

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

Confirm `NOTES` appears before `DEPRECIATION_REPORT` in the output key list.

---

## PHASE 4 — COMMIT

```
git add -A
git commit -m "feat: move Notes before Depreciation Schedule in financial statement package order, all entity types"
git push origin master
```

Report the commit hash.
