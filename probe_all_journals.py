# READ-ONLY probe — every adjusting journal for the two entangled entities.
#
#   cd /opt/statementhub/.claude/worktrees/bas-tb-desync
#   python3 manage.py shell < probe_all_journals.py
#
# Pure read + print. No mutation of any kind.
#
# WHY. probe_entangled_journals.py found only two journals touching Veronica
# 3565: JE-001 (opening balance) and JE-003 ("balanced to bank account"). Elio
# recalled one of those rows as legitimate depreciation, and no depreciation
# journal touches 3565. So either the depreciation journal lands elsewhere in
# the year, or it does not exist. This lists every journal for the year to
# settle that from the record rather than from memory.
#
# FIRST VERSION WAS WRONG. It looked entities up by entity_name and filtered
# financial years on the literal label "2026". Veronica came back "NO SUCH
# FINANCIAL YEAR" even though the audit had just printed year_label 2026 for
# her — because .first() on the name matched a DIFFERENT Entity row sharing it.
# Never look these up by name: the audit gives primary keys, so use them, and
# walk every financial year rather than guessing a label.

from decimal import Decimal

from core.models import AdjustingJournal, Entity, FinancialYear

D = Decimal
RULE = "=" * 78

# Primary keys straight from the audit output — not names.
TARGETS = [
    ("Veronica Cerratti", "e0833e29-665b-49ea-914c-3632bd848524"),
    ("Habteslassie", "d82ed91d-63a3-459e-a03c-b7a2ac755d07"),
]


def money(v):
    return f"{v:>14,.2f}"


def show_journal(j):
    who = getattr(j.created_by, "username", "—")
    poster = getattr(j.posted_by, "username", "—")
    print(f"\n    {j.journal_date}  ref={j.reference_number or '—':<10} "
          f"type={j.journal_type:<16} status={j.status}")
    print(f"      description : {(j.description or '').strip()[:160]}")
    if (j.narration or "").strip():
        print(f"      narration   : {j.narration.strip()[:160]}")
    print(f"      created_by={who}  posted_by={poster}  "
          f"totals Dr{money(j.total_debit)} Cr{money(j.total_credit)}")
    if j.is_trust_distribution:
        print("      is_trust_distribution = True")
    for ln in j.lines.all():
        note = f"   ({ln.description.strip()[:40]})" if (ln.description or "").strip() else ""
        print(f"        {ln.account_code:<10} Dr{money(ln.debit)} Cr{money(ln.credit)}  "
              f"{ln.account_name[:38]}{note}")


print(RULE)
print("ALL ADJUSTING JOURNALS — read-only")
print(RULE)

# ── First: expose any duplicate-name entities, which is what broke v1 ──
print("\nENTITIES SHARING THESE NAMES (the v1 bug):")
for label, _pk in TARGETS:
    matches = Entity.objects.filter(entity_name__icontains=label).order_by("entity_name")
    print(f"\n  '{label}' matches {matches.count()}:")
    for e in matches:
        fys = FinancialYear.objects.filter(entity=e).order_by("start_date")
        years = ", ".join(f"{f.year_label}({f.status})" for f in fys) or "no financial years"
        client = getattr(e.client, "name", "—")
        print(f"    [{e.pk}] {e.entity_name!r}  type={e.entity_type}  client={client}")
        print(f"      years: {years}")

# ── Then the journals, by primary key, across every year ──
for label, pk in TARGETS:
    entity = Entity.objects.filter(pk=pk).first()
    if not entity:
        print(f"\n{label} [{pk}]: ENTITY NOT FOUND")
        continue

    print("\n" + RULE)
    print(f"{entity.entity_name}  [{entity.pk}]")
    print(RULE)

    for fy in FinancialYear.objects.filter(entity=entity).order_by("start_date"):
        journals = AdjustingJournal.objects.filter(
            financial_year=fy
        ).select_related("created_by", "posted_by").prefetch_related(
            "lines"
        ).order_by("journal_date", "reference_number")

        print(f"\n  {fy.year_label}  ({fy.start_date} .. {fy.end_date}, "
              f"status={fy.status})   journals: {journals.count()}")

        for j in journals:
            show_journal(j)

        dep = [
            j for j in journals
            if "deprec" in (j.description or "").lower()
            or "deprec" in (j.narration or "").lower()
            or "deprec" in (j.journal_type or "").lower()
            or any("deprec" in (l.account_name or "").lower()
                   or "deprec" in (l.description or "").lower()
                   for l in j.lines.all())
        ]
        if dep:
            print(f"\n    ** {len(dep)} journal(s) in {fy.year_label} mention depreciation:")
            for j in dep:
                print(f"       {j.journal_date} ref={j.reference_number} — "
                      f"{(j.description or '').strip()[:80]}")
        elif journals:
            print(f"    (no journal in {fy.year_label} mentions depreciation)")

print("\n" + RULE)
print("Nothing was written.")
print(RULE)
