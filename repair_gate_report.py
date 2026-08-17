# READ-ONLY report + backup for the Repair Gate. Writes NO database changes.
#
#   cd /opt/statementhub/.claude/worktrees/bas-tb-desync
#   python3 manage.py shell < repair_gate_report.py
#
# It writes ONE file — a timestamped backup snapshot under data_fixes/ — and
# touches nothing in the database. That snapshot is step 3's prerequisite:
# "a backup of the affected rows written to data_fixes/ first".
#
# WHY THERE IS NO --apply MODE YET, DELIBERATELY.
#
# Elio approved reversing JE-003 (Dr 3565 Loan - Director 164,680.00 /
# Cr 2000 Cash at bank 164,680.00, 2026-01-30, "balanced to bank account").
# But that journal has TWO legs. Removing only the 3565 leg leaves the trial
# balance out of balance by 164,680.00; removing both changes Cash at bank on a
# live client file. Nothing gathered so far tells us what account 2000 currently
# holds or should hold — the audit cannot say, because bank-mapped codes are
# excluded from its variance comparison by design.
#
# Writing an apply path now would mean guessing at a client's cash-at-bank
# balance. So this reports 2000 in full and stops. The apply script gets written
# once the 2000 column below has been read and Elio has said what it should be.
#
# THE TARGET STATE, for reference when that happens:
#
#   manual_journal rows on the account should equal the sum of POSTED journal
#   lines on it, after the reversal. Everything else in those rows is
#   accumulated bank posting and belongs in a source='bank_statement' row.
#
#     Veronica 3565    journal target  Dr        0.00 / Cr  23,897.37  (JE-001 only)
#     Habteslassie 4080 journal target Dr        0.00 / Cr   9,445.36  (JE-001 only)
#
#   The bank row should NOT be hand-written. Create an EMPTY source
#   ='bank_statement', is_adjustment=False row, which clears the account out of
#   the `unbacked` map, and then let _recalculate_bank_tb_lines(fy) populate it —
#   that is the tested primitive, and it computes 237,464.00 for Habteslassie
#   rather than the 246,536.00 the row holds, so the double-post disappears
#   without anyone deciding it should.

import json
import os
from decimal import Decimal

from django.core.serializers.json import DjangoJSONEncoder

from core.models import (
    AdjustingJournal, Entity, FinancialYear, JournalLine, TrialBalanceLine,
)
from core.views import _bank_tb_totals
from core.txn_periods import entity_financial_years

D = Decimal
RULE = "=" * 78

# (label, entity pk, entangled account code)
TARGETS = [
    ("Veronica Cerratti Pty Ltd", "e0833e29-665b-49ea-914c-3632bd848524", "3565"),
    ("Daniel Habteslassie", "d82ed91d-63a3-459e-a03c-b7a2ac755d07", "4080"),
]
# The other leg of JE-003, and the bank side generally.
CASH_CODE = "2000"
# Journals approved for reversal, by reference.
REVERSING = {"JE-003"}
# Timestamp comes from the shell, not from Python, so the filename is stable
# across a re-run within the same second and obvious in shell history.
STAMP = os.environ.get("REPAIR_STAMP", "unstamped")


def money(v):
    if v is None:
        return "          none"
    return f"{v:>14,.2f}"


backup = {"targets": [], "note": "Repair Gate backup — TB rows and journals "
                                 "for the two entangled accounts plus cash at bank."}

print(RULE)
print(f"REPAIR GATE REPORT — read-only  (stamp={STAMP})")
print(RULE)

for label, pk, code in TARGETS:
    entity = Entity.objects.filter(pk=pk).first()
    if not entity:
        print(f"\n{label}: ENTITY NOT FOUND")
        continue
    fys = entity_financial_years(entity)

    for fy in FinancialYear.objects.filter(entity=entity).order_by("start_date"):
        totals = _bank_tb_totals(fy, fys)
        if code not in totals["unbacked"]:
            continue

        print("\n" + RULE)
        print(f"{entity.entity_name}  {fy.year_label}  account {code}")
        print(RULE)

        want = totals["accounts"].get(code, {"debit": D("0"), "credit": D("0")})

        # ── current rows on the entangled account ──
        rows = list(TrialBalanceLine.objects.filter(
            financial_year=fy, account_code=code).order_by("source", "pk"))
        print(f"\n  CURRENT ROWS ON {code}")
        for r in rows:
            print(f"    [{r.pk}] {r.source:<16} adj={str(r.is_adjustment):<5} "
                  f"Dr{money(r.debit)} Cr{money(r.credit)}  {r.account_name}")

        # ── posted journal lines, split by whether they are being reversed ──
        lines = list(JournalLine.objects.filter(
            journal__financial_year=fy, account_code=code,
        ).select_related("journal"))
        keep_dr = keep_cr = rev_dr = rev_cr = D("0")
        print(f"\n  POSTED JOURNAL LINES ON {code}")
        for ln in lines:
            j = ln.journal
            if j.status != "posted":
                print(f"    (skipped, status={j.status}) {j.reference_number}")
                continue
            reversing = (j.reference_number or "") in REVERSING
            tag = "REVERSE" if reversing else "keep"
            print(f"    {tag:<8} {j.reference_number:<8} {j.journal_date}  "
                  f"Dr{money(ln.debit)} Cr{money(ln.credit)}  "
                  f"{(j.description or '').strip()[:50]}")
            if reversing:
                rev_dr += ln.debit or D("0")
                rev_cr += ln.credit or D("0")
            else:
                keep_dr += ln.debit or D("0")
                keep_cr += ln.credit or D("0")

        print(f"\n  TARGET STATE FOR {code}")
        print(f"    manual_journal rows should become   Dr{money(keep_dr)} Cr{money(keep_cr)}")
        print(f"    a new bank_statement row should hold Dr{money(want['debit'])} "
              f"Cr{money(want['credit'])}")
        print(f"    (reversal removes                   Dr{money(rev_dr)} Cr{money(rev_cr)})")

        # ── the other leg: cash at bank ──
        cash_rows = list(TrialBalanceLine.objects.filter(
            financial_year=fy, account_code=CASH_CODE).order_by("source", "pk"))
        print(f"\n  ACCOUNT {CASH_CODE} — THE OTHER LEG OF THE REVERSAL")
        if not cash_rows:
            print(f"    (no rows on {CASH_CODE} at all)")
        cash_dr = cash_cr = D("0")
        for r in cash_rows:
            print(f"    [{r.pk}] {r.source:<16} adj={str(r.is_adjustment):<5} "
                  f"Dr{money(r.debit)} Cr{money(r.credit)}  {r.account_name}")
            cash_dr += r.debit or D("0")
            cash_cr += r.credit or D("0")
        print(f"    {'TOTAL':<48}Dr{money(cash_dr)} Cr{money(cash_cr)}")
        print(f"    is {CASH_CODE} a bank-mapped code here? "
              f"{'YES' if CASH_CODE in totals['bank_codes'] else 'no'}"
              f"   (bank_codes={sorted(totals['bank_codes'])})")
        cash_lines = list(JournalLine.objects.filter(
            journal__financial_year=fy, account_code=CASH_CODE,
        ).select_related("journal"))
        print(f"    journal lines on {CASH_CODE}: {len(cash_lines)}")
        for ln in cash_lines:
            j = ln.journal
            tag = "REVERSE" if (j.reference_number or "") in REVERSING else "keep"
            print(f"      {tag:<8} {j.reference_number:<8} [{j.status}] "
                  f"Dr{money(ln.debit)} Cr{money(ln.credit)}")
        print(f"\n    >>> DECISION NEEDED: reversing JE-003 removes Cr "
              f"{rev_dr:,.2f} from {CASH_CODE}.")
        print(f"        What should {CASH_CODE} read after the repair? Until that is")
        print("        answered, an apply script would be guessing at a client's")
        print("        cash-at-bank balance.")

        # ── backup payload ──
        backup["targets"].append({
            "entity": entity.entity_name,
            "entity_pk": str(entity.pk),
            "financial_year": fy.year_label,
            "financial_year_pk": str(fy.pk),
            "account_code": code,
            "tb_rows": list(TrialBalanceLine.objects.filter(
                financial_year=fy, account_code__in=[code, CASH_CODE]).values()),
            "journals": list(AdjustingJournal.objects.filter(
                financial_year=fy).values()),
            "journal_lines": list(JournalLine.objects.filter(
                journal__financial_year=fy).values()),
            "computed": {
                "journal_keep_debit": str(keep_dr),
                "journal_keep_credit": str(keep_cr),
                "reversal_debit": str(rev_dr),
                "reversal_credit": str(rev_cr),
                "bank_target_debit": str(want["debit"]),
                "bank_target_credit": str(want["credit"]),
            },
        })

path = f"data_fixes/repair_gate_backup_{STAMP}.json"
with open(path, "w") as fh:
    json.dump(backup, fh, indent=2, cls=DjangoJSONEncoder)

print("\n" + RULE)
print(f"BACKUP WRITTEN: {path}")
print("No database changes were made.")
print(RULE)
