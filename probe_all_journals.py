# READ-ONLY probe — every adjusting journal for the two entangled entities.
#
#   cd /opt/statementhub/.claude/worktrees/bas-tb-desync
#   python3 manage.py shell < probe_all_journals.py
#
# Pure read + print. No mutation of any kind.
#
# WHY. probe_entangled_journals.py found only two journals touching Veronica
# 3565: JE-001 (opening balance) and JE-003 ("balanced to bank account"). Elio
# recalled one of these rows as legitimate depreciation, and no depreciation
# journal touches 3565 at all. So either the depreciation journal exists and
# lands elsewhere, or it does not exist. This lists every journal for the year
# so that question is answered by the record rather than by memory — and shows
# what JE-002 and anything after JE-003 actually are.

from decimal import Decimal

from core.models import AdjustingJournal, Entity, FinancialYear

D = Decimal
RULE = "=" * 78

TARGETS = [
    ("Veronica Cerratti Pty Ltd", "2026"),
    ("Daniel Habteslassie", "2026"),
]


def money(v):
    return f"{v:>14,.2f}"


print(RULE)
print("ALL ADJUSTING JOURNALS — read-only")
print(RULE)

for entity_name, year_label in TARGETS:
    entity = Entity.objects.filter(entity_name=entity_name).first()
    if not entity:
        print(f"\n{entity_name}: NOT FOUND")
        continue
    fy = FinancialYear.objects.filter(entity=entity, year_label=year_label).first()
    if not fy:
        print(f"\n{entity_name} {year_label}: NO SUCH FINANCIAL YEAR")
        continue

    journals = AdjustingJournal.objects.filter(
        financial_year=fy
    ).select_related("created_by", "posted_by").prefetch_related(
        "lines"
    ).order_by("journal_date", "reference_number")

    print("\n" + RULE)
    print(f"{entity.entity_name}  {fy.year_label}   journals: {journals.count()}")
    print(RULE)

    for j in journals:
        who = getattr(j.created_by, "username", "—")
        poster = getattr(j.posted_by, "username", "—")
        print(f"\n  {j.journal_date}  ref={j.reference_number or '—':<10} "
              f"type={j.journal_type:<16} status={j.status}")
        print(f"    description : {(j.description or '').strip()[:160]}")
        if (j.narration or "").strip():
            print(f"    narration   : {j.narration.strip()[:160]}")
        print(f"    created_by={who}  posted_by={poster}  "
              f"totals Dr{money(j.total_debit)} Cr{money(j.total_credit)}")
        if j.is_trust_distribution:
            print("    is_trust_distribution = True")
        for ln in j.lines.all():
            note = f"   ({ln.description.strip()[:40]})" if (ln.description or "").strip() else ""
            print(f"      {ln.account_code:<10} Dr{money(ln.debit)} Cr{money(ln.credit)}  "
                  f"{ln.account_name[:38]}{note}")

    # Anything that looks like depreciation, by any spelling, anywhere in the year.
    dep = [
        j for j in journals
        if "deprec" in (j.description or "").lower()
        or "deprec" in (j.narration or "").lower()
        or "deprec" in (j.journal_type or "").lower()
        or any("deprec" in (l.account_name or "").lower()
               or "deprec" in (l.description or "").lower()
               for l in j.lines.all())
    ]
    print(f"\n  JOURNALS MENTIONING DEPRECIATION: {len(dep)}")
    for j in dep:
        print(f"    {j.journal_date} ref={j.reference_number} — "
              f"{(j.description or '').strip()[:80]}")
        for ln in j.lines.all():
            print(f"      {ln.account_code:<10} Dr{money(ln.debit)} Cr{money(ln.credit)}  "
                  f"{ln.account_name[:38]}")
    if not dep:
        print("    (none — no journal in this year mentions depreciation anywhere)")

print("\n" + RULE)
print("Nothing was written.")
print(RULE)
