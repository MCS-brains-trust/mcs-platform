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
#
# WHY .values() AND NOT THE MODEL. Task 7 added amended_since_lodgement /
# amended_at / amended_by to BASPeriod, and migration 0142 has deliberately NOT
# been applied to production — an unmerged branch's migration would leave the
# live database carrying a column main knows nothing about. So this worktree's
# ORM knows three columns the production table lacks, and any ordinary BASPeriod
# queryset dies with "column core_basperiod.amended_since_lodgement does not
# exist". A getattr() guard cannot help: the failure is in the SELECT, long
# before attribute access.
#
# So name every column explicitly. .values() emits exactly these and nothing
# else, which is what makes this runnable against the pre-0142 schema. Do not
# switch it back to model instances until 0142 has been deployed.
#
# label/short_label are model properties, unavailable on a values() dict, so the
# period label is rebuilt here from period_type + period_number.

from decimal import Decimal

from core.bas_utils import calculate_gst_for_period
from core.models import BASPeriod, Entity, FinancialYear

D = Decimal
RULE = "=" * 78

TARGETS = [
    ("Veronica Cerratti", "e0833e29-665b-49ea-914c-3632bd848524"),
    ("Habteslassie", "d82ed91d-63a3-459e-a03c-b7a2ac755d07"),
]


# Every column named here exists in the production table. Adding a Task 7 field
# to this list is what would break the probe again.
PERIOD_COLUMNS = (
    "id", "period_type", "period_number", "period_start", "period_end",
    "status", "lodged_at", "lodged_by__username",
    "snapshot_1a", "snapshot_1b", "snapshot_net", "override_reason",
)

MONTH_ABBR = ("Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
              "Jan", "Feb", "Mar", "Apr", "May", "Jun")


def money(v):
    if v is None:
        return "          none"
    return f"{v:>14,.2f}"


def period_label(row):
    """What BASPeriod.short_label would return, without the model instance."""
    n = row["period_number"]
    if row["period_type"] == "quarterly":
        return f"Q{n}"
    return MONTH_ABBR[n - 1] if 1 <= n <= 12 else f"P{n}"


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
        periods = list(BASPeriod.objects.filter(
            financial_year=fy
        ).order_by("period_type", "period_number").values(*PERIOD_COLUMNS))

        by_type = {}
        for row in periods:
            by_type[row["period_type"]] = by_type.get(row["period_type"], 0) + 1
        composition = ", ".join(f"{k}={v}" for k, v in sorted(by_type.items())) or "—"
        print(f"\n  {fy.year_label} (status={fy.status})   "
              f"BASPeriod rows: {len(periods)}   [{composition}]")
        if len(by_type) > 1:
            print("    NOTE: this year holds MORE THAN ONE period type. Overlapping")
            print("    quarterly and monthly rows cover the same dates, which is the")
            print("    ambiguity flagged in resolve_bas_period_for_txn's docstring —")
            print("    a live case, not a hypothetical one.")

        if not periods:
            print("    (none — no period has ever been opened, so nothing was lodged)")
            continue

        lodged_rows = [r for r in periods if r["status"] == "lodged"]
        print(f"    lodged: {len(lodged_rows)} of {len(periods)}")

        for row in periods:
            if row["status"] != "lodged":
                continue
            print(f"\n    {period_label(row):<6} {row['period_type']:<10} "
                  f"{row['period_start']} .. {row['period_end']}  status=lodged")
            print(f"      lodged {row['lodged_at']} by "
                  f"{row['lodged_by__username'] or '—'}")
            if (row["override_reason"] or "").strip():
                print(f"      override reason: {row['override_reason'].strip()[:120]}")

            recomputed = calculate_gst_for_period(
                fy, row["period_start"], row["period_end"])["bas_data"]
            now_1a = recomputed.get("1A")
            now_1b = recomputed.get("1B")
            now_net = recomputed.get("gst_payable")

            print(f"      {'':<12}{'as lodged':>16}{'today':>16}{'difference':>16}")
            for name, was, now in (
                ("1A GST sales", row["snapshot_1a"], now_1a),
                ("1B GST purch", row["snapshot_1b"], now_1b),
                ("net GST", row["snapshot_net"], now_net),
            ):
                diff = (was - now) if (was is not None and now is not None) else None
                print(f"      {name:<12}{money(was)}{money(now)}{money(diff)}")

            if row["snapshot_1a"] is not None and now_1a is not None:
                over = row["snapshot_1a"] - now_1a
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
