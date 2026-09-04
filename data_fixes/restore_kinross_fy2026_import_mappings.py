"""Write Kinross FY2026's QuickBooks import mappings into the staged record.

The accountant mapped all 44 rows of the QuickBooks import four times over and
the Confirm button stayed grey each time. The page they were working in had been
loaded before the import_wizard.js fix shipped, so it was still running the old
gate -- which never re-evaluated after an entity account was assigned. Their work
was real, but it lived only in the DOM: the wizard holds mappings in the browser
until Confirm posts the form, and Confirm was exactly what they could not press.

Reloading to pick up the fixed script would have thrown all 44 away.

These mappings are transcribed from the page they pasted, and every one was
checked before writing: all 44 source codes match a staged line, every entity
account code exists in Kinross's chart of accounts, and every standard code
resolves to an AccountMapping. Writing them into StagedImport.lines means the
reloaded page renders already mapped, under the fixed script, with the button
live.

Touches staging data only -- no trial balance line, journal or statement figure
is created or changed by this. The import itself is still posted from the wizard,
by hand, with its preview and audit trail.

Usage:
    python3 data_fixes/restore_kinross_fy2026_import_mappings.py --dry-run
    python3 data_fixes/restore_kinross_fy2026_import_mappings.py --commit
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone as dt_timezone

import django

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.db import transaction  # noqa: E402

from core.models import (  # noqa: E402
    AccountMapping, Entity, EntityChartOfAccount, StagedImport,
)

# source account_code -> (entity COA code, AccountMapping standard_code)
MAPPINGS = {
    "53": ("2000", "BS-CA-001"), "121": ("2001", "BS-CA-001"),
    "1150040014": ("2003", "BS-CA-001"), "58": ("2101", "BS-CA-002"),
    "1150040015": ("3325", "BS-CL-003"), "73": ("3048", "BS-CL-001"),
    "120": ("3102", "BS-CL-001"), "36": ("3325", "BS-CL-003"),
    "42": ("3380", "BS-CL-003"), "44": ("3380", "BS-CL-003"),
    "35": ("3326", "BS-CL-003"), "122": ("3244", "BS-CL-002"),
    "1150040008": ("3245", "BS-CL-002"), "1150040009": ("3325", "BS-CL-003"),
    "24": ("3565", "BS-CL-008"), "1150040012": ("3565", "BS-CL-008"),
    "1": ("630", "IS-REV-001"), "100": ("1927", "IS-EXP-008"),
    "61": ("1800", "IS-EXP-016"), "60": ("1800", "IS-EXP-016"),
    "119": ("1830", "IS-EXP-016"), "62": ("1740", "IS-EXP-016"),
    "3": ("1510", "IS-EXP-001"), "4": ("1515", "IS-EXP-002"),
    "5": ("1545", "IS-EXP-016"), "96": ("1685", "IS-EXP-016"),
    "20": ("1790", "IS-EXP-011"), "22": ("1760", "IS-EXP-016"),
    "67": ("1657", "IS-EXP-008"), "63": ("1809", "IS-EXP-010"),
    "69": ("1809", "IS-EXP-010"), "79": ("1809", "IS-EXP-010"),
    "78": ("1809", "IS-EXP-010"), "64": ("1809", "IS-EXP-010"),
    "13": ("1750", "IS-EXP-016"), "98": ("1740", "IS-EXP-016"),
    "88": ("1951", "IS-EXP-008"), "16": ("1865", "IS-EXP-012"),
    "66": ("1565", "IS-EXP-016"), "17": ("1915", "IS-EXP-008"),
    "7": ("1925", "IS-EXP-016"), "18": ("1940", "IS-EXP-011"),
    "34": ("1755", "IS-EXP-009"), "21": ("1989", "IS-EXP-016"),
}


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--commit", action="store_true")
    args = ap.parse_args()

    entity = Entity.objects.filter(entity_name__icontains="kinross").first()
    if entity is None:
        raise SystemExit("No entity matching 'kinross'.")
    fy = entity.financial_years.filter(year_label="2026").first()
    if fy is None:
        raise SystemExit("Kinross has no FY2026.")
    staged = StagedImport.objects.filter(financial_year=fy).first()
    if staged is None:
        raise SystemExit("No staged import on Kinross FY2026 — has it been committed already?")

    chart = {c.account_code: c.account_name
             for c in EntityChartOfAccount.objects.filter(entity=entity)}
    std = {m.standard_code: m for m in AccountMapping.objects.all()}

    # Refuse rather than half-apply: a partly mapped staging row is worse than
    # an unmapped one, because it looks finished.
    problems = []
    for line in staged.lines:
        code = line.get("account_code")
        if code not in MAPPINGS:
            problems.append(f"staged line {code!r} has no mapping supplied")
            continue
        coa, sc = MAPPINGS[code]
        if coa not in chart:
            problems.append(f"{code}: COA {coa} is not in the entity chart")
        if sc not in std:
            problems.append(f"{code}: {sc} is not an AccountMapping")
    if problems:
        for p in problems:
            print("  " + p)
        raise SystemExit(f"{len(problems)} problem(s) — nothing written.")

    updated = []
    for line in staged.lines:
        coa, sc = MAPPINGS[line["account_code"]]
        m = std[sc]
        updated.append({
            **line,
            "entity_acct_code": coa,
            "entity_acct_name": chart[coa],
            "mapped_id": str(m.pk),
            "mapped_label": m.line_item_label,
        })

    print(f"Kinross Builders FY2026 · {staged.provider_name} · {len(updated)} lines")
    for line in updated[:5]:
        print(f"   {line['account_code']:>12}  {str(line['account_name'])[:30]:30} "
              f"-> {line['entity_acct_code']:>5} {line['entity_acct_name'][:26]:26} "
              f"| {line['mapped_label'][:30]}")
    print(f"   ... and {len(updated) - 5} more")

    before_mapped = sum(1 for l in staged.lines if l.get("entity_acct_code"))
    print(f"\n   rows with an entity account: {before_mapped} -> {len(updated)}")

    if args.dry_run:
        print("\nDry run — nothing written. Re-run with --commit to apply.")
        return

    stamp = datetime.now(dt_timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        f"kinross_fy2026_staged_import_pre_mapping_{stamp}.json",
    )
    with open(path, "w") as fh:
        json.dump({"staged_import_id": str(staged.pk), "lines": staged.lines}, fh, indent=2)
    print(f"\nBacked up the staged lines to {path}")

    with transaction.atomic():
        StagedImport.objects.filter(pk=staged.pk).update(lines=updated)
    print("Applied. Reload the Review Import page — every row will be mapped and "
          "Confirm will be live.")


if __name__ == "__main__":
    main()
