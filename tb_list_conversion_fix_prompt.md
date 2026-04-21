# TB UI View: Move list() Conversion After All Queryset Operations

## PHASE 1 — AUDIT (read files, do not write any code yet)

In `core/views.py`, inside `financial_year_detail`, find every place where `tb_lines.filter()`, `tb_lines.exclude()`, `tb_lines.update()`, or any other Django queryset method is called AFTER the `list(tb_lines)` conversion that was added in the previous commit.

Report every line number where a queryset method is called on `tb_lines` after the `list()` conversion. The error is at line 1855: `tb_lines.filter(source='bank_statement').exists()` — but there may be others.

Do not proceed until all occurrences are reported.

---

## PHASE 2 — FIX

The `list()` conversion must happen AFTER all queryset operations on `tb_lines`.

Find the last queryset operation (`.filter()`, `.exclude()`, `.update()`, `.exists()`, etc.) on `tb_lines` in `financial_year_detail`. Move the `list()` conversion and the `_cy`/`_py` tagging loop to immediately after that last queryset operation — but before the `display_dr`/`display_cr` annotation loop.

The correct order must be:

1. All `tb_lines.filter()`, `tb_lines.exclude()`, `tb_lines.update()` calls — queryset operations
2. `tb_lines = list(tb_lines)` — convert to list
3. `_cy`/`_py` tagging loop — annotate each line
4. `display_dr`/`display_cr` annotation loop — uses `line._cy`
5. Section grouping loop
6. Grand totals loop

Do not change any other logic.

---

## PHASE 3 — VERIFY

Run on the server:

```
source /opt/statementhub/venv/bin/activate && cd /opt/statementhub && sudo systemctl restart gunicorn
```

Then open `/years/dcdd1d3e-379d-42d6-a364-d2cc075f1235/` in the browser and confirm:
- No 500 error
- TB tab loads successfully
- "Out of balance" warning is gone
- Total Debits ≈ Total Credits + Net Profit (7,919)

---

## PHASE 4 — COMMIT

```
git add -A
git commit -m "fix: move list() conversion after all queryset operations on tb_lines in financial_year_detail"
git push origin master
```

Report the commit hash.
