"""Restate Elliott Jaques JE-001 as a Cashbook journal with GST split out.

JE-001 was keyed GST-inclusive with no 3380 line, which left the P&L
GST-inclusive and the ATO liability off the balance sheet. It is the only
journal on the platform in this shape, its financial year is draft and
unlocked, and nothing has been lodged -- so it is restated in place rather
than converted through general tooling.

Backs up the journal and its TB lines first. Reposts through the normal path
(reverse via source_journal, then _post_journal_to_tb) rather than patching TB
rows, so the trial balance is rebuilt exactly as a new journal would build it.

REQUIRES the cashbook GST migration (JournalLine.tax_code / .gst_amount /
.gst_override / .is_gst_control and the CASHBOOK journal type). The script
refuses with a clear message if it is not applied.

Usage:
    python3 data_fixes/restate_elliott_jaques_je001.py --dry-run
    python3 data_fixes/restate_elliott_jaques_je001.py --commit
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal

import django

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.db import transaction  # noqa: E402

from core.bas_utils import calculate_gst_for_period, get_period_dates  # noqa: E402
from core.models import (  # noqa: E402
    AdjustingJournal, BASPeriod, Entity, JournalLine, TrialBalanceLine,
)

# The entity was re-cased from "ELLIOTT JAQUES" by the person-name
# capitalisation work on 2026-08-31, so match on case rather than the literal.
ENTITY_NAME = "elliott jaques"

EXPECTED_GROSS_TOTAL = Decimal("23187.00")
EXPECTED_CONTROL_NET = Decimal("-1689.23")   # closing_balance on 3380
EXPECTED_1A = Decimal("2107.91")
EXPECTED_1B = Decimal("418.68")
EXPECTED_JOURNAL_LINES = 11
EXPECTED_TB_LINES = 10

TAX_CODES = {
    "105": "GST",
    "1510": "INP", "1800": "INP", "1804": "INP", "1808": "INP",
    "1809": "INP", "1845": "INP", "1940": "INP", "1946": "INP",
    "4080": "N-T",
}


def require_migration():
    """Fail loudly and early rather than deep inside the atomic block.

    Introspects the actual table, not ``JournalLine._meta``: this script is
    run from a checkout that already defines the fields, so the model always
    has them. What matters is whether the migration reached *this database*.
    """
    from django.db import connection

    with connection.cursor() as cursor:
        have = {
            col.name for col in
            connection.introspection.get_table_description(
                cursor, JournalLine._meta.db_table,
            )
        }
    missing = {"tax_code", "gst_amount", "gst_override", "is_gst_control"} - have
    if missing:
        sys.exit(
            "REFUSING: this database has not had the cashbook GST migration "
            f"applied -- JournalLine is missing {sorted(missing)}. Merge and "
            "deploy feat/cashbook-gst-journals, run `manage.py migrate`, then "
            "run this script."
        )
    if not hasattr(AdjustingJournal.JournalType, "CASHBOOK"):
        sys.exit("REFUSING: AdjustingJournal.JournalType has no CASHBOOK member.")


def get_entity():
    matches = list(Entity.objects.filter(entity_name__iexact=ENTITY_NAME))
    if len(matches) != 1:
        sys.exit(
            f"REFUSING: expected exactly one entity named {ENTITY_NAME!r}, "
            f"found {[e.entity_name for e in matches]}."
        )
    return matches[0]


def backup(journal, fy):
    stamp = datetime.now(dt_timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        f"elliott_jaques_je001_pre_gst_split_{stamp}.json",
    )
    payload = {
        "journal": {
            "id": str(journal.id),
            "reference_number": journal.reference_number,
            "journal_type": journal.journal_type,
            "status": journal.status,
            "journal_date": str(journal.journal_date),
            "description": journal.description,
            "total_debit": str(journal.total_debit),
            "total_credit": str(journal.total_credit),
        },
        "lines": [
            {
                "id": str(l.id), "line_number": l.line_number,
                "account_code": l.account_code, "account_name": l.account_name,
                "description": l.description,
                "debit": str(l.debit), "credit": str(l.credit),
            }
            for l in journal.lines.order_by("line_number", "id")
        ],
        "tb_lines": [
            {
                "id": str(t.id), "account_code": t.account_code,
                "account_name": t.account_name,
                "debit": str(t.debit), "credit": str(t.credit),
                "closing_balance": str(t.closing_balance),
                "source": t.source,
                "source_journal": str(t.source_journal_id) if t.source_journal_id else None,
            }
            for t in TrialBalanceLine.objects.filter(financial_year=fy).order_by("account_code")
        ],
    }
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=2)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not (args.commit or args.dry_run):
        ap.error("pass --dry-run or --commit")

    require_migration()

    entity = get_entity()
    fy = entity.financial_years.first()
    journal = fy.adjusting_journals.get(reference_number="JE-001")

    if fy.is_locked:
        sys.exit("REFUSING: financial year is locked.")
    if fy.status not in ("draft", "in_progress"):
        sys.exit(f"REFUSING: financial year status is {fy.status!r}, not draft.")
    if BASPeriod.objects.filter(financial_year=fy, status="lodged").exists():
        sys.exit("REFUSING: a BAS period for this year is already lodged.")
    if journal.journal_type == AdjustingJournal.JournalType.CASHBOOK:
        sys.exit("Already restated -- journal is a Cashbook journal.")

    if journal.total_debit != EXPECTED_GROSS_TOTAL:
        sys.exit(
            f"REFUSING: JE-001 totals {journal.total_debit}, expected "
            f"{EXPECTED_GROSS_TOTAL}. The journal has changed since this "
            f"script was written -- re-read it before restating."
        )
    if journal.lines.count() != EXPECTED_JOURNAL_LINES:
        sys.exit(
            f"REFUSING: JE-001 has {journal.lines.count()} lines, expected "
            f"{EXPECTED_JOURNAL_LINES}."
        )
    unmapped = sorted(
        {l.account_code for l in journal.lines.all()} - set(TAX_CODES)
    )
    if unmapped:
        sys.exit(
            f"REFUSING: no tax code decided for account(s) {unmapped}. Add them "
            f"to TAX_CODES deliberately rather than letting the chart decide."
        )

    path = backup(journal, fy)
    print(f"backup written: {path}")

    if args.dry_run:
        for line in journal.lines.order_by("line_number", "id"):
            code = line.account_code
            print(f"  {code:<6} {line.account_name[:28]:<29} "
                  f"gross {max(line.debit, line.credit):>10} "
                  f"tax {TAX_CODES.get(code, '(chart)')}")
        print("dry run -- nothing written")
        return

    # Imported here, not at module scope: core.views pulls in a large slice of
    # the app, and the dry run has no business loading it.
    from core.gst_journal import split_cashbook_journal
    from core.views import _post_journal_to_tb, _reverse_journal_tb_lines

    with transaction.atomic():
        _reverse_journal_tb_lines(journal)

        journal.journal_type = AdjustingJournal.JournalType.CASHBOOK
        journal.save(update_fields=["journal_type"])

        for line in journal.lines.all():
            line.tax_code = TAX_CODES.get(line.account_code, "")
            line.gst_amount = Decimal("0")
            line.save(update_fields=["tax_code", "gst_amount"])

        split_cashbook_journal(journal)
        _post_journal_to_tb(journal, fy)
        journal.recalculate_totals()

    journal.refresh_from_db()
    control = TrialBalanceLine.objects.get(financial_year=fy, account_code="3380")
    start, end = get_period_dates(fy, "quarterly", 2)
    bas = calculate_gst_for_period(fy, start, end)["bas_data"]

    print(f"journal totals  Dr {journal.total_debit}  Cr {journal.total_credit}")
    print(f"3380 closing    {control.closing_balance}")
    print(f"BAS Q2          1A {bas['1A']}  1B {bas['1B']}  net {bas['gst_payable']}")

    failures = []
    if journal.total_debit != EXPECTED_GROSS_TOTAL:
        failures.append(f"journal total {journal.total_debit} != {EXPECTED_GROSS_TOTAL}")
    if control.closing_balance != EXPECTED_CONTROL_NET:
        failures.append(f"3380 closing {control.closing_balance} != {EXPECTED_CONTROL_NET}")
    if bas["1A"] != EXPECTED_1A:
        failures.append(f"1A {bas['1A']} != {EXPECTED_1A}")
    if bas["1B"] != EXPECTED_1B:
        failures.append(f"1B {bas['1B']} != {EXPECTED_1B}")
    if -control.closing_balance != bas["gst_payable"]:
        failures.append(
            f"3380 {-control.closing_balance} does not tie to BAS net {bas['gst_payable']}"
        )
    if failures:
        sys.exit("POST-CHECK FAILED:\n  " + "\n  ".join(failures))
    print("all post-checks passed; 3380 ties to the BAS")


if __name__ == "__main__":
    main()
