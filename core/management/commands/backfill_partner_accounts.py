"""Bring partnership charts onto per-partner accounts.

Two jobs, both idempotent:

1. Genericise the live partnership template (``ChartOfAccount``) to match the
   seed file. The seed was built from a real three-partner firm and carried
   their first names, so every partnership created from it inherited accounts
   named for strangers. Editing data/all_accounts.json fixes future imports
   only; this fixes the table those imports already populated.

2. Provision per-partner accounts for partnership entities that already have
   partners, since they were created before the service covered them.

Dry-run by default. Pass --apply to write.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

# Kept in step with data/all_accounts.json.
TEMPLATE_DROP = [
    "4000.01", "4000.02",
    "4003.01", "4003.02",
    "4006.01", "4006.02",
    "4007", "4007.01", "4007.02",
    "4009.01", "4009.02",
    "4054.01", "4054.02", "4054.03",
]
TEMPLATE_RENAME = {
    "4000": "Opening balance",
    "4006": "Capital contribution",
}


class Command(BaseCommand):
    help = "Genericise the partnership chart template and backfill per-partner accounts"

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true",
            help="Write the changes. Without this the command only reports.",
        )

    def handle(self, *args, **options):
        from core.models import ChartOfAccount, Entity, EntityOfficer
        from core.beneficiary_account_service import provision_beneficiary_accounts

        apply = options["apply"]
        prefix = "" if apply else "[dry-run] "

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"{prefix}1. Partnership template"))

        drop_qs = ChartOfAccount.objects.filter(
            entity_type="partnership", account_code__in=TEMPLATE_DROP)
        for row in drop_qs.order_by("account_code"):
            self.stdout.write(f"   drop   {row.account_code:9} {row.account_name}")
        drop_count = drop_qs.count()

        renames = []
        for code, new_name in TEMPLATE_RENAME.items():
            row = ChartOfAccount.objects.filter(
                entity_type="partnership", account_code=code).first()
            if row and row.account_name != new_name:
                renames.append((row, new_name))
                self.stdout.write(
                    f"   rename {row.account_code:9} {row.account_name!r} -> {new_name!r}")

        if apply:
            with transaction.atomic():
                drop_qs.delete()
                for row, new_name in renames:
                    row.account_name = new_name
                    row.save(update_fields=["account_name"])
        self.stdout.write(
            f"   {prefix}{drop_count} dropped, {len(renames)} renamed\n")

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"{prefix}2. Existing partnership entities"))

        total = 0
        for entity in Entity.objects.filter(entity_type="partnership").order_by("entity_name"):
            partners = EntityOfficer.objects.filter(
                entity=entity, role=EntityOfficer.OfficerRole.PARTNER,
            ).order_by("display_order")
            self.stdout.write(
                f"   {entity.entity_name} — {partners.count()} partner(s)")
            for partner in partners:
                if apply:
                    created = provision_beneficiary_accounts(partner.pk)
                    total += created
                    self.stdout.write(
                        f"       {partner.full_name}: {created} account(s) created")
                else:
                    self.stdout.write(
                        f"       {partner.full_name}: would provision")

        if apply:
            self.stdout.write(self.style.SUCCESS(
                f"\nDone — {total} per-partner accounts created."))
        else:
            self.stdout.write(
                "\nDry run. Re-run with --apply to write these changes.")
