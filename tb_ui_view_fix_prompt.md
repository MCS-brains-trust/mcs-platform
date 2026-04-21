# TB UI View: Fix Rollover Line Doubling in financial_year_detail

## PHASE 1 — AUDIT (read files, do not write any code yet)

In `core/views.py`, find the `financial_year_detail` view (the one that renders `financial_year_detail.html` and passes `total_debit` and `total_credit` to the template at around line 1902).

Report:

1. The exact line where `tb_lines` is fetched for this view — confirm whether it excludes `source='rollover'` or not
2. The exact line where the display_dr/display_cr annotation loop runs (around line 1492) — confirm it iterates `tb_lines` and whether `tb_lines` is a list or queryset at that point
3. The exact line where the section grouping loop runs (around line 1505) — confirm it also iterates `tb_lines`
4. Confirm whether rollover lines are included in `tb_lines` and whether their `closing_balance` is being added to `display_dr`/`display_cr` alongside the tb_import lines for the same account

Do not proceed until all Phase 1 findings are reported.

---

## PHASE 2 — FIX

Apply the same rollover/tb_import separation logic used in `trial_balance_download`.

### Step A — Convert tb_lines to a list and tag _cy/_py

Find where `tb_lines` is fetched for the `financial_year_detail` view. Immediately after the queryset, convert to a list and tag each line:

```python
tb_lines = list(tb_lines)  # convert queryset to list so attributes persist
for line in tb_lines:
    if line.source == 'rollover':
        line._cy = Decimal('0')
        line._py = (line.prior_debit or Decimal('0')) - (line.prior_credit or Decimal('0'))
    else:
        line._cy = line.closing_balance or Decimal('0')
        line._py = Decimal('0')
```

### Step B — Update the display_dr/display_cr annotation loop

Find the loop that sets `line.display_dr` and `line.display_cr` (around line 1492). Replace it to use `line._cy` instead of `line.closing_balance`:

```python
for line in tb_lines:
    cy = line._cy
    if cy > 0:
        line.display_dr = cy
        line.display_cr = Decimal('0')
    elif cy < 0:
        line.display_dr = Decimal('0')
        line.display_cr = abs(cy)
    else:
        line.display_dr = line.debit if line.debit else Decimal('0')
        line.display_cr = line.credit if line.credit else Decimal('0')
```

### Step C — Update the prior year totals calculation

Find the grand totals loop (around line 1630) where `pdr = line.prior_debit` and `pcr = line.prior_credit` are used. Replace with `_py`-based logic:

```python
py = getattr(line, '_py', None)
if py is None:
    pdr = line.prior_debit or Decimal('0')
    pcr = line.prior_credit or Decimal('0')
else:
    if py > 0:
        pdr = py
        pcr = Decimal('0')
    elif py < 0:
        pdr = Decimal('0')
        pcr = abs(py)
    else:
        pdr = Decimal('0')
        pcr = Decimal('0')
```

Apply this replacement wherever `line.prior_debit` and `line.prior_credit` are used in the grand totals loop for this view.

---

## PHASE 3 — VERIFY

Run on the server:

```
source /opt/statementhub/venv/bin/activate && cd /opt/statementhub && sudo systemctl restart gunicorn
```

Then open the Scarton Family Trust 2024 financial year in StatementHub and check the TB tab. Confirm:
- Total Debits and Total Credits — difference equals Net Profit (7,919)
- No "Out of balance" warning
- No doubled figures in any line

---

## PHASE 4 — COMMIT

```
git add -A
git commit -m "fix: apply rollover/tb_import separation to TB UI view in financial_year_detail"
git push origin master
```

Report the commit hash.
