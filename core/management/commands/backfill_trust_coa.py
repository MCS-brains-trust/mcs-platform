"""
Backfill EntityChartOfAccount for trust entities that currently have none.

Idempotent: delegates to EntityChartOfAccount.seed_from_template(), which
itself returns 0 and does nothing if the entity already has any
EntityChartOfAccount rows. Each entity is wrapped in its own
transaction.atomic() so one failure does not roll back the rest.

Usage:
    python3 manage.py backfill_trust_coa
    python3 manage.py backfill_trust_coa --dry-run
    python3 manage.py backfill_trust_coa -v 2     # per-entity output
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Entity, EntityChartOfAccount


class Command(BaseCommand):
    help = "Seed EntityChartOfAccount for trust entities that have none"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview without writing to the database",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        verbosity = options.get("verbosity", 1)

        trusts = Entity.objects.filter(entity_type=Entity.EntityType.TRUST)
        total = trusts.count()

        seeded = 0
        skipped = 0
        errors = 0

        label = "[DRY RUN] " if dry_run else ""
        self.stdout.write(f"{label}Processing {total} trust entities...")

        for entity in trusts.iterator():
            try:
                has_rows = EntityChartOfAccount.objects.filter(entity=entity).exists()
            except Exception as exc:
                errors += 1
                self.stderr.write(self.style.ERROR(
                    f"  ERROR   {entity.id}  {entity.entity_name}: {exc}"
                ))
                continue

            if has_rows:
                skipped += 1
                if verbosity >= 2:
                    self.stdout.write(
                        f"  SKIP        {entity.id}  {entity.entity_name}  (already has COA)"
                    )
                continue

            if dry_run:
                seeded += 1
                if verbosity >= 2:
                    self.stdout.write(
                        f"  WOULD SEED  {entity.id}  {entity.entity_name}"
                    )
                continue

            try:
                with transaction.atomic():
                    result = EntityChartOfAccount.seed_from_template(entity)
                seeded += 1
                if verbosity >= 2:
                    self.stdout.write(
                        f"  SEEDED      {entity.id}  {entity.entity_name}  ({result} accounts)"
                    )
            except Exception as exc:
                errors += 1
                self.stderr.write(self.style.ERROR(
                    f"  ERROR       {entity.id}  {entity.entity_name}: {exc}"
                ))

        summary_style = self.style.WARNING if dry_run else self.style.SUCCESS
        verb = "would be seeded" if dry_run else "entities seeded"
        self.stdout.write(summary_style(
            f"{label}{seeded} {verb} | {skipped} skipped | {errors} errors"
        ))
