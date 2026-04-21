# Depreciation Report: Show "0" for Zero CWDV and Net Depreciation

## PHASE 1 — AUDIT (read files, do not write any code yet)

In `core/fs_template_service.py` inside `_generate_depreciation_report`, report:

1. The exact line where `closing_wdv` is formatted in the asset row `vals` list
2. The exact line where `net_dep` is calculated and formatted in the "Net depreciation line" paragraph
3. The exact line where the grand total Net Depreciation is formatted in the grand totals paragraph
4. The exact `_fmt` function definition — confirm it returns `""` for zero values

Do not proceed until all Phase 1 findings are reported.

---

## PHASE 2 — FIX

Add one new formatter function immediately after the existing `_fmt` function:

```python
def _fmt_zero(val):
    """Like _fmt but returns '0' instead of '' for zero values."""
    if val is None:
        return "0"
    if val == 0:
        return "0"
    return f"{val:,.0f}"
```

Apply `_fmt_zero` in these five locations only — do not change `_fmt` anywhere else:

1. **Asset row `vals` list** — replace `_fmt(asset.closing_wdv)` with `_fmt_zero(asset.closing_wdv)` (CWDV column)
2. **Subtotal row** — replace `_fmt(cat_cwdv)` with `_fmt_zero(cat_cwdv)` (CWDV column only)
3. **Grand totals row** — replace `_fmt(grand_cwdv)` with `_fmt_zero(grand_cwdv)` (CWDV column only)
4. **Net depreciation paragraph** — replace `_fmt(net_dep)` with `_fmt_zero(net_dep)` so "Net Depreciation: 0" renders instead of "Net Depreciation: "
5. **Grand totals Net Depreciation paragraph** — same replacement for the grand total net depreciation figure

Do not apply `_fmt_zero` to any other column (Total Cost, OWDV, Deprec, Priv, etc.).

---

## PHASE 3 — VERIFY

Run on the server (Claude Code cannot run this locally — no SECRET_KEY on Windows dev):

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

Report the full output and confirm no errors.

---

## PHASE 4 — COMMIT

```
git add -A
git commit -m "fix: show 0 for zero CWDV and zero Net Depreciation in depreciation report"
git push origin master
```

Report the commit hash.
