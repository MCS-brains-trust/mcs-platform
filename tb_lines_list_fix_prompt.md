# Comparative TB: Fix Queryset Re-evaluation Losing _cy/_py Attributes

## PHASE 1 — AUDIT (read files, do not write any code yet)

In `core/views.py` around line 11057, confirm:

1. That `tb_lines` is iterated twice:
   - First loop to tag `_cy`/`_py` attributes on each line
   - Second loop for section grouping and grand totals calculation
2. That `tb_lines` is a Django queryset (not a list) at the point of the first iteration — meaning each iteration re-evaluates the queryset and returns new Python objects, losing any attributes set in the previous iteration

Do not proceed until confirmed.

---

## PHASE 2 — FIX

In `trial_balance_download` (views.py), convert `tb_lines` to a list immediately after the queryset is defined, before any iteration.

Replace:

```python
tb_lines = TrialBalanceLine.objects.filter(
    financial_year=fy
).select_related('mapped_line_item').order_by('account_code', 'source')
```

With:

```python
tb_lines = list(TrialBalanceLine.objects.filter(
    financial_year=fy
).select_related('mapped_line_item').order_by('account_code', 'source'))
```

This ensures the same Python objects are used in both loops so `_cy`/`_py` attributes set in the first loop persist into the second loop and into `_aggregate_tb_lines`.

Do not change anything else.

---

## PHASE 3 — VERIFY

Run on the server:

```
source /opt/statementhub/venv/bin/activate && cd /opt/statementhub && sudo systemctl restart gunicorn
```

Then download the Comparative TB from the UI for Scarton Family Trust 2024 and confirm:
- CY Dr and CY Cr — difference should equal Net Profit (7,919)
- No doubled figures
- Prior year figures correct (PY difference = 147,214)

---

## PHASE 4 — COMMIT

```
git add -A
git commit -m "fix: convert tb_lines queryset to list before tagging _cy/_py to prevent re-evaluation"
git push origin master
```

Report the commit hash.
