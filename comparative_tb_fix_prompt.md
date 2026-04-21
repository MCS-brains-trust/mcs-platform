# Comparative TB Download: Fix Rollover Line Doubling

## PHASE 1 — AUDIT (read files, do not write any code yet)

In `core/views.py` around line 11053, confirm:

1. The exact line where `tb_lines` is fetched for `trial_balance_download` — confirm it does not exclude `source='rollover'`
2. Find the `_aggregate_tb_lines` function — report its file location, line number, and how it currently computes `_agg_dr`, `_agg_cr`, `_agg_prior_dr`, `_agg_prior_cr` per line
3. Report exactly which fields on `TrialBalanceLine` are used to populate those four accumulators

Do not proceed until all Phase 1 findings are reported.

---

## PHASE 2 — FIX

### Step A — Fetch lines ordered by source

In `trial_balance_download` (views.py around line 11053), change the queryset to order by source so rollover lines always come first:

```python
tb_lines = TrialBalanceLine.objects.filter(
    financial_year=fy
).select_related('mapped_line_item').order_by('account_code', 'source')
```

### Step B — Tag each line with _cy and _py before the section loop

Immediately after fetching `tb_lines` and before the section grouping loop, add:

```python
for line in tb_lines:
    if line.source == 'rollover':
        line._cy = Decimal('0')
        line._py = line.prior_debit - line.prior_credit
    else:
        line._cy = line.closing_balance or Decimal('0')
        line._py = Decimal('0')
```

### Step C — Fix the grand totals loop

In the grand totals calculation loop (around line 11082), replace:

```python
cb = line.closing_balance or Decimal('0')
if cb > 0:
    grand_dr += cb
elif cb < 0:
    grand_cr += abs(cb)
else:
    grand_dr += line.debit or Decimal('0')
    grand_cr += line.credit or Decimal('0')
grand_prior_dr += line.prior_debit or Decimal('0')
grand_prior_cr += line.prior_credit or Decimal('0')
```

With:

```python
cy = line._cy
if cy > 0:
    grand_dr += cy
elif cy < 0:
    grand_cr += abs(cy)
py = line._py
if py > 0:
    grand_prior_dr += py
elif py < 0:
    grand_prior_cr += abs(py)
```

### Step D — Update _aggregate_tb_lines

Update `_aggregate_tb_lines` to use `line._cy` and `line._py` instead of `line.closing_balance` and `line.prior_debit`/`line.prior_credit` when computing `_agg_dr`, `_agg_cr`, `_agg_prior_dr`, `_agg_prior_cr`.

Use the Phase 1 finding 3 to identify the exact fields and replace them consistently. The rule is:
- CY debit: `line._cy` if positive
- CY credit: `abs(line._cy)` if negative
- PY debit: `line._py` if positive
- PY credit: `abs(line._py)` if negative

---

## PHASE 3 — VERIFY

Run on the server:

```
source /opt/statementhub/venv/bin/activate && cd /opt/statementhub && sudo systemctl restart gunicorn
```

Then download the Comparative TB from the UI for Scarton Family Trust 2024 (use the Preview Trial Balance button) and confirm:
- 2024 Dr total and Cr total — the difference should equal Net Profit (7,919)
- No doubled figures in any line
- Prior year figures correct

---

## PHASE 4 — COMMIT

```
git add -A
git commit -m "fix: exclude rollover lines from Comparative TB download, apply cy/py separation logic"
git push origin master
```

Report the commit hash.
