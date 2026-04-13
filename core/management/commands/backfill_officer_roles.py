from django.core.management.base import BaseCommand

from core.models import EntityOfficer


class Command(BaseCommand):
    help = "Backfill EntityOfficer.roles JSONField from legacy role CharField."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be migrated without writing.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        migrated = 0
        skipped = 0
        errors = 0

        officers = EntityOfficer.objects.all()
        for officer in officers:
            if officer.roles:
                skipped += 1
                continue
            if not officer.role:
                self.stderr.write(
                    f"ERROR: {officer.full_name} (pk={officer.pk}) has no role or roles"
                )
                errors += 1
                continue
            if not dry_run:
                officer.roles = [officer.role]
                officer.save(update_fields=["roles"])
            migrated += 1

        prefix = "[DRY RUN] " if dry_run else ""
        self.stdout.write(
            f"{prefix}{migrated} officers migrated | "
            f"{skipped} already had roles | "
            f"{errors} errors"
        )
