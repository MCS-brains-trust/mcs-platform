# READ-ONLY probe — did either entity lodge a BAS on income now known to be GST-free?
#
#   cd /opt/statementhub/.claude/worktrees/bas-tb-desync
#   python3 manage.py shell < probe_lodged_bas.py
#
# Pure read + print. No mutation of any kind.
#
# WHY THIS MATTERS MORE THAN THE LEDGER. Elio confirmed 2026-08-17 that both
# entities are medical practitioners, so ~99% of their income is GST-free. The
# July 2026 scripts that reclassified their income to "GST Free Income" were
# therefore CORRECT, and the trial balance is the stale side.
#
# But the BAS is computed from PendingTransaction records, not from the ledger.
# So any BAS lodged BEFORE that reclassification was computed from transactions
# still carrying GST on income — and would have reported GST on supplies that
# were never taxable. If such a period was lodged, these clients remitted GST
# they did not owe, and the lodged snapshot is now the only record of what was
# reported.
#
# BASPeriod.snapshot_1a / _1b / _net freeze the figures as lodged. This compares
# each lodged period's snapshot against the same period recomputed from today's
# transactions. A snapshot_1A materially above the recomputed 1A is money the
# client may be entitled to recover by amendment.
#
# This is exactly what Task 7's amended_since_lodgement flag was built to
# surface going forward; this probe looks backwards at what already happened.

from decimal import Decimal

from core.bas_utils import calculate_gst_for_period
from core.models import BASPeriod, Entity, FinancialYear

D = Decimal
RULE = "=" * 78

TARGETS = [
    ("Veronica Cerratti", "e0833e29-665b-49ea-914c-3632bd848524"),
    ("Habteslassie", "d82ed91d-63a3-459e-a03c-b7a2ac755d07"),
]


def money(v):
    if v is None:
        return "          none"
    return f"{v:>14,.2f}"


print(RULE)
print("LODGED BAS PERIODS vs TODAY'S TRANSACTIONS — read-only")
print(RULE)

for label, pk in TARGETS:
    entity = Entity.objects.filter(pk=pk).first()
    if not entity:
        print(f"\n{label} [{pk}]: ENTITY NOT FOUND")
        continue

    print("\n" + RULE)
    print(f"{entity.entity_name}  [{entity.pk}]   "
          f"bas_frequency={getattr(entity, 'bas_frequency', '—')}   "
          f"gst_registered={entity.is_gst_registered}")
    print(RULE)

    for fy in FinancialYear.objects.filter(entity=entity).order_by("start_date"):
        periods = BASPeriod.objects.filter(
            financial_year=fy).order_by("period_type", "period_number")
        print(f"\n  {fy.year_label} (status={fy.status})   "
              f"BASPeriod rows: {periods.count()}")

        if not periods:
            print("    (none — no period has ever been opened, so nothing was lodged)")
            continue

        for bp in periods:
            flag = ""
            # Task 7's field; guard so this probe still runs on a checkout without it.
            if getattr(bp, "amended_since_lodgement", False):
                flag = "   [AMENDED SINCE LODGEMENT]"
            print(f"\n    {bp.label:<16} status={bp.status}{flag}")
            if bp.status != "lodged":
                continue

            lodged_by = getattr(bp.lodged_by, "username", "—")
            print(f"      lodged {bp.lodged_at} by {lodged_by}")
            if (bp.override_reason or "").strip():
                print(f"      override reason: {bp.override_reason.strip()[:120]}")

            recomputed = calculate_gst_for_period(
                fy, bp.period_start, bp.period_end)["bas_data"]
            now_1a = recomputed.get("1A")
            now_1b = recomputed.get("1B")
            now_net = recomputed.get("gst_payable")

            print(f"      {'':<12}{'as lodged':>16}{'today':>16}{'difference':>16}")
            for name, was, now in (
                ("1A GST sales", bp.snapshot_1a, now_1a),
                ("1B GST purch", bp.snapshot_1b, now_1b),
                ("net GST", bp.snapshot_net, now_net),
            ):
                diff = (was - now) if (was is not None and now is not None) else None
                print(f"      {name:<12}{money(was)}{money(now)}{money(diff)}")

            if bp.snapshot_1a is not None and now_1a is not None:
                over = bp.snapshot_1a - now_1a
                if over > 0:
                    print(f"\n      >>> 1A was lodged {over:,.2f} HIGHER than today's")
                    print("          transactions support. On GST-free medical income that")
                    print("          is GST reported but not owed — a candidate for")
                    print("          amendment and recovery.")
                elif over < 0:
                    print(f"\n      >>> 1A was lodged {abs(over):,.2f} LOWER than today's")
                    print("          transactions support — an underpayment, not a refund.")
                else:
                    print("\n      >>> 1A unchanged. Nothing to amend on sales.")

print("\n" + RULE)
print("Nothing was written.")
print(RULE)
