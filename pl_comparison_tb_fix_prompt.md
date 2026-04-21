# P&L Comparison: Handiledger vs StatementHub — TB Fix

## Step 1 — Read the Handiledger reference PDF

Read the file at `C:\Users\Elio\mcs-platform\handiledger_reference\Financial Statements SCARFT.pdf`

Extract and report ALL figures from the Profit and Loss Statement pages:

1. Every Income line item with its 2024 and 2023 figures
2. Total Income 2024 and 2023
3. Every Expense line item with its 2024 and 2023 figures
4. Total Expenses 2024 and 2023
5. Net Profit 2024 and 2023

Report figures exactly as they appear. Do not summarise.

---

## Step 2 — Read StatementHub TB from the server

Run this on the server and report the full output:

```
source /opt/statementhub/venv/bin/activate && cd /opt/statementhub && python3 manage.py shell -c "
from core.models import TrialBalanceLine, FinancialYear
fy = FinancialYear.objects.get(pk='dcdd1d3e-379d-42d6-a364-d2cc075f1235')
print('=== INCOME (code < 1000) ===')
for l in TrialBalanceLine.objects.filter(financial_year=fy, account_code__lt='1000').exclude(source='rollover').order_by('account_code'):
    print(f'{l.account_code} {l.account_name} Dr:{l.debit} Cr:{l.credit} closing:{l.closing_balance}')
print()
print('=== EXPENSES (code 1000-1999) ===')
for l in TrialBalanceLine.objects.filter(financial_year=fy, account_code__gte='1000', account_code__lt='2000').exclude(source='rollover').order_by('account_code'):
    print(f'{l.account_code} {l.account_name} Dr:{l.debit} Cr:{l.credit} closing:{l.closing_balance}')
"
```

---

## Step 3 — Compare and identify differences

Produce a side-by-side comparison table:

| Account | Account Name | Handiledger 2024 | StatementHub 2024 | Match? |
|---------|-------------|-----------------|-------------------|--------|

Flag every line that differs. Identify what TB lines need to be added, removed, or corrected to make StatementHub match Handiledger exactly.

---

## Step 4 — Apply corrections on the server

Apply all corrections directly using `python3 manage.py shell -c` commands on the server.

After each correction confirm what was changed.

---

## Step 5 — Verify TB is in balance

Run this on the server and report the output:

```
source /opt/statementhub/venv/bin/activate && cd /opt/statementhub && python3 manage.py shell -c "
from core.models import TrialBalanceLine, FinancialYear
from decimal import Decimal
fy = FinancialYear.objects.get(pk='dcdd1d3e-379d-42d6-a364-d2cc075f1235')
lines = TrialBalanceLine.objects.filter(financial_year=fy).exclude(source='rollover')
total_dr = sum(l.debit for l in lines)
total_cr = sum(l.credit for l in lines)
print('Total Dr:', total_dr)
print('Total Cr:', total_cr)
print('Difference:', total_dr - total_cr)
print('Net Profit:', total_cr - total_dr)
"
```

TB is correct when Difference = 0.00 and Net Profit matches Handiledger.

---

## Step 6 — Regenerate financial statements

Once TB is confirmed in balance, run:

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
