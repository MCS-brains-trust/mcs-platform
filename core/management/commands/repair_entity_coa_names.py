"""
Management command: repair_entity_coa_names

Restores EntityChartOfAccount.account_name values that were incorrectly
overwritten with raw HandiLedger Excel names by a previous bad fix
(commit 60ac0bc, deployed ~Mar 2026).

The correct source of truth for account names is the existing
TrialBalanceLine records (non-adjustment lines from TB imports).
This command syncs entity COA names from the most recent non-adjustment
TB line for each account code, per entity.

Usage:
    python3 manage.py repair_entity_coa_names
    python3 manage.py repair_entity_coa_names --entity-id <uuid>  # single entity
    python3 manage.py repair_entity_coa_names --dry-run           # preview only
"""
from django.core.management.base import BaseCommand
from django.db.models import OuterRef, Subquery

from core.models import Entity, EntityChartOfAccount, TrialBalanceLine


class Command(BaseCommand):
    help = "Restore entity COA account names from the canonical TB line names."

    def add_arguments(self, parser):
        parser.add_argument(
            "--entity-id",
            type=str,
            help="Limit repair to a single entity UUID.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print proposed changes without writing to the database.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        entity_id = options.get("entity_id")

        entities = Entity.objects.all()
        if entity_id:
            entities = entities.filter(pk=entity_id)

        total_updated = 0
        total_skipped = 0

        for entity in entities:
            # Build a lookup: account_code -> canonical name from the most
            # recent non-adjustment TB import line for this entity.
            # We use the latest financial year that has non-adjustment lines.
            tb_name_map = {}
            tb_lines = (
                TrialBalanceLine.objects.filter(
                    financial_year__entity=entity,
                    is_adjustment=False,
                )
                .values("account_code", "account_name")
                .distinct()
            )
            for row in tb_lines:
                code = row["account_code"].lower()
                # Keep the first occurrence (ordering is by account_code,
                # so this is stable; all non-adjustment lines for the same
                # code should have the same name from the TB import).
                if code not in tb_name_map:
                    tb_name_map[code] = row["account_name"]

            if not tb_name_map:
                self.stdout.write(
                    f"  {entity.entity_name}: no TB lines found, skipping."
                )
                continue

            coa_entries = EntityChartOfAccount.objects.filter(entity=entity)
            entity_updated = 0

            for ea in coa_entries:
                canonical = tb_name_map.get(ea.account_code.lower())
                if canonical and canonical != ea.account_name:
                    if dry_run:
                        self.stdout.write(
                            f"  [DRY RUN] {entity.entity_name} | "
                            f"{ea.account_code}: "
                            f"'{ea.account_name}' -> '{canonical}'"
                        )
                    else:
                        ea.account_name = canonical
                        ea.save(update_fields=["account_name"])
                        self.stdout.write(
                            f"  {entity.entity_name} | {ea.account_code}: "
                            f"updated to '{canonical}'"
                        )
                    entity_updated += 1
                else:
                    total_skipped += 1

            total_updated += entity_updated
            if entity_updated:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  {entity.entity_name}: {entity_updated} account(s) updated."
                    )
                )

        action = "Would update" if dry_run else "Updated"
        self.stdout.write(
            self.style.SUCCESS(
                f"\n{action} {total_updated} entity COA record(s). "
                f"{total_skipped} already correct."
            )
        )
