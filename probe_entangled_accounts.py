# READ-ONLY probe — the two accounts blocking the BAS-to-ledger Repair Gate.
#
# Run on the SERVER, from the worktree that holds this branch (the aggregation
# helper it imports does not exist on main):
#
#   cd /opt/statementhub/.claude/worktrees/bas-tb-desync
#   python3 manage.py shell < probe_entangled_accounts.py
#
# Pure read + print. No .save(), no .create(), no mutation of any kind.
#
# Goal: answer step 2 of the Repair Gate — for each entangled account, how much
# of the manual_journal row is accumulated bank posting, and how much is genuine
# journal?
#
# The arithmetic that matters. Bank postings accumulate with += into whatever
# row _get_or_create_tb_line hands back, which on these accounts is a
# manual_journal row. So:
#
#     row balance  =  genuine journal  +  accumulated bank postings
#
# `wanted` below is the accumulated bank postings, computed by the same
# _bank_tb_totals the rebuild itself uses — not a reimplementation. Therefore:
#
#     genuine journal  =  row balance  −  wanted
#
# If that residual is zero the row is pure bank money and the repair is
# mechanical: move it to a source='bank_statement' row. If it is non-zero, the
# residual is the part that needs an accounting decision.

from decimal import Decimal

from core.models import Entity, FinancialYear, TrialBalanceLine
from core.views import _bank_tb_totals
from core.txn_periods import entity_financial_years, resolve_fy_for_txn
from review.models import PendingTransaction

D = Decimal
ITEMISE = True          # print the contributing transactions one per line
RULE = "=" * 78


def money(v):
    return f"{v:>14,.2f}"


print(RULE)
print("ENTANGLED ACCOUNT PROBE — read-only")
print(RULE)

found_any = False

for entity in Entity.objects.all().order_by("entity_name"):
    fys = entity_financial_years(entity)
    for fy in FinancialYear.objects.filter(entity=entity).order_by("start_date"):
        totals = _bank_tb_totals(fy, fys)

        if not totals["unbacked"]:
            continue
        if not totals["fy_resolvable"]:
            # Same guard the audit applies: on an unresolvable year the
            # aggregation comes back empty for reasons that have nothing to do
            # with the accounts, so any comparison here would be a lie.
            print(f"\n{entity.entity_name} {fy.year_label}: SKIPPED, year not "
                  f"postable (status {fy.status!r})")
            continue

        for code in sorted(totals["unbacked"]):
            found_any = True
            print("\n" + RULE)
            print(f"{entity.entity_name}  [{entity.pk}]")
            print(f"{fy.year_label}  account {code}")
            print(RULE)

            # ── every row currently sitting on this account code ──
            rows = list(TrialBalanceLine.objects.filter(
                financial_year=fy, account_code=code).order_by("source", "pk"))

            print("\n  ROWS PRESENT")
            row_debit = row_credit = D("0")
            for r in rows:
                print(f"    [{r.pk}] source={r.source:<16} "
                      f"is_adjustment={str(r.is_adjustment):<5} "
                      f"Dr{money(r.debit)}  Cr{money(r.credit)}   {r.account_name}")
                row_debit += r.debit or D("0")
                row_credit += r.credit or D("0")
            if not rows:
                print("    (none)")
            print(f"    {'TOTAL':<48}Dr{money(row_debit)}  Cr{money(row_credit)}")

            # ── what the posted transactions say this account should hold ──
            if code == "3380":
                want = totals["gst"]
            else:
                want = totals["accounts"].get(
                    code, {"debit": D("0"), "credit": D("0")})
            want_debit, want_credit = want["debit"], want["credit"]

            print("\n  WHAT THE POSTED TRANSACTIONS SAY (same rule the rebuild uses)")
            print(f"    {'':<48}Dr{money(want_debit)}  Cr{money(want_credit)}")

            # ── the residual: what is not bank money ──
            # Report the debit and credit residuals SEPARATELY. Netting them
            # hides the shape of the problem: a row can be out by +9,072 debit
            # and -9,445 credit — two unrelated ~9k discrepancies — and net to a
            # harmless-looking -373. The first version of this probe printed only
            # the net and made exactly that mistake on Habteslassie 4080.
            residual_debit = row_debit - want_debit
            residual_credit = row_credit - want_credit
            row_net = row_debit - row_credit
            want_net = want_debit - want_credit

            print("\n  RESIDUAL — row minus bank postings, per side")
            print(f"    debit  side  row{money(row_debit)}  bank{money(want_debit)}"
                  f"  residual{money(residual_debit)}")
            print(f"    credit side  row{money(row_credit)}  bank{money(want_credit)}"
                  f"  residual{money(residual_credit)}")
            print(f"    net          row{money(row_net)}  bank{money(want_net)}"
                  f"  residual{money(row_net - want_net)}")

            if residual_debit == 0 and residual_credit == 0:
                print("\n    >>> PURE BANK MONEY. No accounting decision needed: the whole")
                print("        balance is accumulated bank postings. Repair is mechanical —")
                print("        move it to a source='bank_statement' row.")
            else:
                print("\n    >>> MIXED. Unexplained by bank postings:")
                if residual_debit:
                    print(f"          debit  {residual_debit:,.2f}")
                if residual_credit:
                    print(f"          credit {residual_credit:,.2f}")
                print("        Each side needs accounting for separately — they are not")
                print("        necessarily the same entry, and netting them proves nothing.")
                if residual_debit == 0:
                    print("        NOTE: the debit side reconciles exactly. Only the credit")
                    print("        side needs a decision.")
                elif residual_credit == 0:
                    print("        NOTE: the credit side reconciles exactly. Only the debit")
                    print("        side needs a decision.")

            # ── the transactions behind `wanted` ──
            posted = [
                t for t in PendingTransaction.objects.filter(
                    job__entity=entity, is_confirmed=True, posted_to_tb=True,
                    confirmed_code=code,
                ).select_related("job", "job__entity").order_by("date")
                if resolve_fy_for_txn(t, fys) == fy
            ]
            print(f"\n  CONTRIBUTING TRANSACTIONS: {len(posted)}")
            if posted:
                gross = sum(abs(t.amount) for t in posted)
                gst = sum((t.confirmed_gst_amount or D("0")) for t in posted)
                print(f"    gross {money(gross)}   gst {money(gst)}   "
                      f"dates {posted[0].date} .. {posted[-1].date}")
                if ITEMISE:
                    for t in posted:
                        print(f"      {t.date:<12} {abs(t.amount):>12,.2f} "
                              f"gst {(t.confirmed_gst_amount or D('0')):>10,.2f}  "
                              f"{(t.confirmed_tax_type or '—'):<20} "
                              f"{t.description[:44]}")

if not found_any:
    print("\nNo entangled accounts found. Nothing blocking the gate.")

print("\n" + RULE)
print("Nothing was written.")
print(RULE)
