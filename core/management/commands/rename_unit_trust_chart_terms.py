"""
Rename existing unit trust chart entries from "Beneficiary" to "Unit Holder".

Task 11 made EntityChartOfAccount.seed_from_template and
core.beneficiary_account_service overlay "Unit Holder" wording onto the
shared "trust" template for any unit trust chart seeded from now on. Unit
trusts seeded before that change — most importantly Minli Enterprise Unit
Trust — still carry "Opening balance - Beneficiary" on 4000 and its
officer-suffixed children (4000.01, 4000.02, ...), and "Beneficiary current
account" on 4100. This command is the one-off backfill for those.

Chart names are now authoritative for TrialBalanceLine.account_name at
import and rollover (PR #83/#84), and backfill_tb_account_names rewrites
existing rows from the chart. So renaming a chart entry here also rewrites
the matching trial balance rows directly — EXCEPT rows belonging to a
finalised financial year, whose financial statements have already been
issued to the client. Minli's FY2024 and FY2025 are finalised and currently
read "Beneficiary"; those rows are skipped by default, exactly as
backfill_tb_account_names skips finalised years, and are only touched with
--include-finalised.

The chart-of-account rows themselves (EntityChartOfAccount) are entity-level,
not year-scoped, and are always eligible for rename under --apply — they
describe what the chart calls the account going forward, not a specific
year's issued statements.

Only entity_type="trust_unit" entities are ever considered. A discretionary
trust (entity_type="trust") is never touched by this command, even if asked
to via --entity or --include-finalised: its chart is expected to say
"Beneficiary" and must keep saying so.

Usage:
    python3 manage.py rename_unit_trust_chart_terms                 # dry run (default)
    python3 manage.py rename_unit_trust_chart_terms --apply
    python3 manage.py rename_unit_trust_chart_terms --apply --entity "Minli"
    python3 manage.py rename_unit_trust_chart_terms --apply --include-finalised
"""
import re

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Entity, EntityChartOfAccount, TrialBalanceLine

_TERM_RE = re.compile(r"beneficiary", re.IGNORECASE)


def _renamed(name):
    """Swap "Beneficiary" for "Unit Holder", preserving case of the first
    letter and leaving everything else — including any " — Officer Name"
    suffix — untouched."""
    if not name:
        return name

    def repl(match):
        return "Unit Holder" if match.group(0)[0].isupper() else "unit holder"

    return _TERM_RE.sub(repl, name)


class Command(BaseCommand):
    help = (
        "Rename existing unit trust chart-of-account entries (and their "
        "trial balance rows) from 'Beneficiary' to 'Unit Holder'. Dry-run "
        "by default; pass --apply to write."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write the changes. Without this flag nothing is written.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help=(
                "Report what would change without writing anything. This is "
                "also the default behaviour when neither flag is given."
            ),
        )
        parser.add_argument(
            "--entity",
            default="",
            help="Only process unit trust entities whose name contains this text.",
        )
        parser.add_argument(
            "--include-finalised",
            action="store_true",
            help=(
                "Also rewrite trial balance rows in finalised years. Off by "
                "default: renaming an account in a finalised year changes "
                "financial statements that have already been issued."
            ),
        )

    def handle(self, *args, **options):
        # Default is dry-run; --apply is required to write. --dry-run is
        # accepted explicitly too but is never needed to get dry-run
        # behaviour — it can only ever narrow, not widen, what gets written.
        apply_changes = options["apply"] and not options["dry_run"]
        entity_filter = options["entity"]
        include_finalised = options["include_finalised"]

        # Scoped to trust_unit only — a discretionary trust is never a
        # candidate for this command, regardless of any other flag.
        entities = Entity.objects.filter(entity_type="trust_unit").order_by(
            "entity_name"
        )
        if entity_filter:
            entities = entities.filter(entity_name__icontains=entity_filter)

        total_chart = 0
        total_tb = 0
        total_skipped_finalised = 0
        entities_touched = 0

        for entity in entities:
            eca_pending = []
            for eca in EntityChartOfAccount.objects.filter(
                entity=entity, account_name__icontains="beneficiary"
            ):
                new_name = _renamed(eca.account_name)
                if new_name != eca.account_name:
                    eca_pending.append((eca, new_name))

            tb_pending = []
            tb_lines = (
                TrialBalanceLine.objects.filter(
                    financial_year__entity=entity,
                    account_name__icontains="beneficiary",
                )
                .select_related("financial_year")
                .order_by("financial_year__end_date", "account_code")
            )
            for line in tb_lines:
                new_name = _renamed(line.account_name)
                if new_name == line.account_name:
                    continue
                if line.financial_year.status == "finalised" and not include_finalised:
                    total_skipped_finalised += 1
                    continue
                tb_pending.append((line, new_name))

            if not eca_pending and not tb_pending:
                continue

            entities_touched += 1
            self.stdout.write(self.style.MIGRATE_HEADING(entity.entity_name))
            for eca, new_name in eca_pending:
                self.stdout.write(
                    f"  chart  {eca.account_code:<10} "
                    f"{eca.account_name!r} -> {new_name!r}"
                )
            for line, new_name in tb_pending:
                self.stdout.write(
                    f"  tb     {line.financial_year.year_label} "
                    f"{line.account_code:<10} {line.account_name!r} -> {new_name!r}"
                )

            if apply_changes:
                with transaction.atomic():
                    for eca, new_name in eca_pending:
                        eca.account_name = new_name
                        eca.save(update_fields=["account_name"])
                    for line, new_name in tb_pending:
                        line.account_name = new_name
                        line.save(update_fields=["account_name"])

            total_chart += len(eca_pending)
            total_tb += len(tb_pending)

        verb = "renamed" if apply_changes else "would rename"
        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} {total_chart} chart account(s) and {total_tb} trial "
                f"balance row(s) across {entities_touched} entit(ies)."
            )
        )
        if total_skipped_finalised:
            self.stdout.write(
                self.style.WARNING(
                    f"skipped {total_skipped_finalised} trial balance row(s) "
                    f"in finalised years — re-run with --include-finalised "
                    f"to correct those too."
                )
            )
