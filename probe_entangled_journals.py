# READ-ONLY probe — the journals behind the entangled accounts.
#
# Run on the SERVER, from the worktree holding this branch:
#
#   cd /opt/statementhub/.claude/worktrees/bas-tb-desync
#   python3 manage.py shell < probe_entangled_journals.py
#
# Pure read + print. No .save(), no .create(), no mutation of any kind.
#
# WHY THIS EXISTS. probe_entangled_accounts.py compared the trial-balance rows
# against the posted bank transactions and concluded that every debit on
# Veronica 3565 is accumulated bank posting, because the three rows' debits sum
# to exactly the bank total. Elio reads those same rows as recognisable
# journals — an opening-balance import and a depreciation entry. Both cannot be
# true, and the two readings differ by ~$339k on a client's director loan.
#
# TrialBalanceLine has no foreign key to AdjustingJournal, so nothing in the
# rows themselves says which journal produced them. But JournalLine records do
# survive. So this settles it arithmetically rather than by inference:
#
#     TB rows total  -  posted journal lines total  =  what accumulated
#                                                      from bank postings
#
# If the journal lines account for the whole row balance, the rows are pure
# journal, nothing is hiding in them, and the rebuild must not touch them. If
# they fall short, the shortfall is the bank money to move — and it should
# match what probe_entangled_accounts.py called `wanted`.

from decimal import Decimal

from core.models import (
    AdjustingJournal, Entity, FinancialYear, JournalLine, TrialBalanceLine,
)
from core.views import _bank_tb_totals
from core.txn_periods import entity_financial_years

D = Decimal
RULE = "=" * 78


def money(v):
    return f"{v:>14,.2f}"


print(RULE)
print("ENTANGLED ACCOUNT JOURNALS — read-only")
print(RULE)

for entity in Entity.objects.all().order_by("entity_name"):
    fys = entity_financial_years(entity)
    for fy in FinancialYear.objects.filter(entity=entity).order_by("start_date"):
        totals = _bank_tb_totals(fy, fys)
        if not totals["unbacked"] or not totals["fy_resolvable"]:
            continue

        for code in sorted(totals["unbacked"]):
            print("\n" + RULE)
            print(f"{entity.entity_name}  [{entity.pk}]   {fy.year_label}  account {code}")
            print(RULE)

            # ── what the trial balance rows hold ──
            rows = list(TrialBalanceLine.objects.filter(
                financial_year=fy, account_code=code).order_by("source", "pk"))
            tb_debit = sum((r.debit or D("0")) for r in rows)
            tb_credit = sum((r.credit or D("0")) for r in rows)
            print(f"\n  TRIAL BALANCE ROWS ({len(rows)})"
                  f"        Dr{money(tb_debit)}  Cr{money(tb_credit)}")
            for r in rows:
                print(f"    [{r.pk}] {r.source:<16} adj={str(r.is_adjustment):<5} "
                      f"Dr{money(r.debit)}  Cr{money(r.credit)}  {r.account_name}")

            # ── every journal line on this account code, with its parent ──
            lines = list(JournalLine.objects.filter(
                journal__financial_year=fy, account_code=code,
            ).select_related("journal", "journal__created_by",
                             "journal__posted_by").order_by("journal__journal_date"))

            print(f"\n  JOURNAL LINES ON {code}: {len(lines)}")
            jl_debit = jl_credit = D("0")
            jl_debit_posted = jl_credit_posted = D("0")
            for ln in lines:
                j = ln.journal
                who = getattr(j.created_by, "username", "—")
                print(f"\n    Dr{money(ln.debit)}  Cr{money(ln.credit)}   "
                      f"[status={j.status}]  {j.journal_date}  ref={j.reference_number or '—'}")
                print(f"      journal type : {j.journal_type}")
                print(f"      description  : {(j.description or '').strip()[:150]}")
                if (j.narration or "").strip():
                    print(f"      narration    : {j.narration.strip()[:150]}")
                if (ln.description or "").strip():
                    print(f"      line note    : {ln.description.strip()[:150]}")
                print(f"      created_by   : {who}   journal totals "
                      f"Dr{money(j.total_debit)} Cr{money(j.total_credit)}")
                # the rest of the entry — what this journal did elsewhere
                others = [o for o in j.lines.all() if o.pk != ln.pk]
                if others:
                    print(f"      other lines in this journal ({len(others)}):")
                    for o in others[:12]:
                        print(f"        {o.account_code:<10} Dr{money(o.debit)} "
                              f"Cr{money(o.credit)}  {o.account_name[:40]}")
                    if len(others) > 12:
                        print(f"        … and {len(others) - 12} more")
                jl_debit += ln.debit or D("0")
                jl_credit += ln.credit or D("0")
                if j.status == "posted":
                    jl_debit_posted += ln.debit or D("0")
                    jl_credit_posted += ln.credit or D("0")

            if not lines:
                print("    (none — no journal line anywhere touches this account code)")

            # ── the decisive comparison ──
            if code == "3380":
                want = totals["gst"]
            else:
                want = totals["accounts"].get(
                    code, {"debit": D("0"), "credit": D("0")})

            print("\n  THE COMPARISON")
            print(f"    trial balance rows          Dr{money(tb_debit)}  Cr{money(tb_credit)}")
            print(f"    journal lines (all)         Dr{money(jl_debit)}  Cr{money(jl_credit)}")
            print(f"    journal lines (posted only) Dr{money(jl_debit_posted)}  "
                  f"Cr{money(jl_credit_posted)}")
            print(f"    bank postings say           Dr{money(want['debit'])}  "
                  f"Cr{money(want['credit'])}")

            unaccounted_dr = tb_debit - jl_debit_posted
            unaccounted_cr = tb_credit - jl_credit_posted
            print(f"\n    rows minus posted journals  Dr{money(unaccounted_dr)}  "
                  f"Cr{money(unaccounted_cr)}")
            print("      ^ this is what accumulated from bank postings, if anything did")

            if unaccounted_dr == want["debit"] and unaccounted_cr == want["credit"]:
                print("\n    >>> CONSISTENT with the plan's model: the shortfall against the")
                print("        journals equals the bank postings exactly. The rows are")
                print("        journal + bank, and the bank part is what moves.")
            elif jl_debit_posted == tb_debit and jl_credit_posted == tb_credit:
                print("\n    >>> THE ROWS ARE PURE JOURNAL. The posted journal lines account")
                print("        for the whole balance, so no bank posting ever landed here.")
                print("        Creating a bank_statement row for the bank figure would ADD")
                print("        money the ledger already holds — it would double the account.")
                print("        Do not rebuild this account until that is resolved.")
            else:
                print("\n    >>> NEITHER MODEL FITS CLEANLY. The rows are not pure journal,")
                print("        and the shortfall does not equal the bank postings either.")
                print("        Read the journals above before deciding anything.")

print("\n" + RULE)
print("Nothing was written.")
print(RULE)
