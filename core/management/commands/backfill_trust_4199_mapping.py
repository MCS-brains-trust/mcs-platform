"""Backfill trust 4199 (Undistributed income) onto BS-EQ-005.

Migration 0148_trust_4199_mapping already does this at deploy time. This
command exists as a standalone, rerunnable equivalent for an environment
where that migration ran before AccountMapping BS-EQ-005 existed (the
migration's own get_or_create covers a fresh database, but an operator may
still want to re-verify or re-apply the correction by hand without faking a
migration re-run).

Idempotent: only ever moves rows onto BS-EQ-005, never away from it.

Usage:
    python3 manage.py backfill_trust_4199_mapping
    python3 manage.py backfill_trust_4199_mapping --dry-run
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = "Correct trust 4199 (Undistributed income) to map to BS-EQ-005 on the master template and every trust entity chart"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview without writing to the database",
        )

    def handle(self, *args, **options):
        from core.models import AccountMapping, ChartOfAccount, EntityChartOfAccount

        dry_run = options["dry_run"]
        label = "[DRY RUN] " if dry_run else ""

        try:
            target = AccountMapping.objects.get(standard_code="BS-EQ-005")
        except AccountMapping.DoesNotExist:
            raise CommandError(
                "AccountMapping BS-EQ-005 does not exist. Run "
                "`manage.py seed_account_mappings` first."
            )

        # Master template.
        tpl = ChartOfAccount.objects.filter(
            entity_type="trust", account_code="4199",
        ).first()
        if tpl is None:
            self.stdout.write(self.style.WARNING(
                f"{label}No master ChartOfAccount row for trust 4199 — "
                "run `manage.py import_chart_of_accounts` first. Skipping "
                "the template correction."
            ))
        elif tpl.maps_to_id == target.id:
            self.stdout.write(f"{label}Master template 4199 already maps to BS-EQ-005.")
        else:
            self.stdout.write(
                f"{label}Master template 4199: "
                f"{getattr(tpl.maps_to, 'standard_code', None)} -> BS-EQ-005"
            )
            if not dry_run:
                tpl.maps_to = target
                tpl.save(update_fields=["maps_to"])

        # Existing trust entity charts.
        wrong_qs = EntityChartOfAccount.objects.filter(
            entity__entity_type="trust", account_code="4199",
        ).exclude(maps_to=target).select_related("entity", "maps_to")

        count = wrong_qs.count()
        self.stdout.write(f"{label}{count} trust entity chart row(s) to correct.")
        for eca in wrong_qs:
            current = getattr(eca.maps_to, "standard_code", None)
            self.stdout.write(
                f"  {eca.entity.entity_name} ({eca.entity_id}): "
                f"{current} -> BS-EQ-005"
            )

        if not dry_run and count:
            with transaction.atomic():
                wrong_qs.update(maps_to=target)

        self.stdout.write(self.style.SUCCESS(f"{label}Done."))
