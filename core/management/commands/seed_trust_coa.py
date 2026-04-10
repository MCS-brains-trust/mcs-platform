"""
Seed the AccountRangeAlias table with the default trust chart of accounts
alias taxonomy.

This is global reference data, not per-entity: one set of rows for
entity_type="trust" shared across every trust in the database. Safe to
run multiple times — uses get_or_create keyed on (entity_type, alias).

Usage:
    python3 manage.py seed_trust_coa            # write rows
    python3 manage.py seed_trust_coa --dry-run  # preview without writing
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import AccountRangeAlias, Entity
from core.trust_coa_seed import TRUST_ACCOUNT_RANGE_ALIASES


class Command(BaseCommand):
    help = "Seed AccountRangeAlias with the default trust chart of accounts alias taxonomy"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be seeded without writing to the database",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        entity_type = Entity.EntityType.TRUST  # "trust"

        total = len(TRUST_ACCOUNT_RANGE_ALIASES)
        created = 0
        skipped = 0
        errors = 0

        if dry_run:
            self.stdout.write(self.style.WARNING(
                f"[DRY RUN] No writes. Checking {total} alias rows for entity_type='{entity_type}'..."
            ))
        else:
            self.stdout.write(
                f"Seeding {total} alias rows for entity_type='{entity_type}'..."
            )

        for row in TRUST_ACCOUNT_RANGE_ALIASES:
            try:
                section, alias, description, dc_sign, range_from, range_to = row
            except ValueError:
                errors += 1
                self.stderr.write(self.style.ERROR(
                    f"Malformed seed row (expected 6 fields): {row!r}"
                ))
                continue

            if dry_run:
                exists = AccountRangeAlias.objects.filter(
                    entity_type=entity_type, alias=alias
                ).exists()
                if exists:
                    skipped += 1
                else:
                    created += 1
                continue

            try:
                with transaction.atomic():
                    _, was_created = AccountRangeAlias.objects.get_or_create(
                        entity_type=entity_type,
                        alias=alias,
                        defaults={
                            "description": description,
                            "dc_sign": dc_sign,
                            "range_from": range_from,
                            "range_to": range_to,
                            "section": section,
                        },
                    )
                    if was_created:
                        created += 1
                    else:
                        skipped += 1
            except Exception as exc:
                errors += 1
                self.stderr.write(self.style.ERROR(
                    f"Failed to seed alias '{alias}': {exc}"
                ))

        if dry_run:
            summary = (
                f"[DRY RUN] Would create {created} new aliases | "
                f"Would skip {skipped} already existing | "
                f"{errors} errors | {total} total rows in seed"
            )
            self.stdout.write(self.style.WARNING(summary))
        else:
            summary = (
                f"Created {created} aliases | "
                f"Skipped {skipped} already existing | "
                f"{errors} errors | {total} total rows in seed"
            )
            self.stdout.write(self.style.SUCCESS(summary))
