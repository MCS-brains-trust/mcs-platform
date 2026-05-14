"""
Management command: seed_general_pool_coa

Adds the two general pool accounts to:
  1. The master ChartOfAccount template (all entity types)
  2. Every existing EntityChartOfAccount that does NOT already have them

New accounts:
  1620  Depreciation - General Pool   section=expenses
  2905  General Pool                  section=non_current_assets

Usage:
    python manage.py seed_general_pool_coa            # dry-run (preview only)
    python manage.py seed_general_pool_coa --apply    # apply changes
"""
from django.core.management.base import BaseCommand
from core.models import ChartOfAccount, EntityChartOfAccount, Entity

ENTITY_TYPES = ["company", "partnership", "sole_trader", "trust"]

NEW_ACCOUNTS = [
    {
        "account_code": "1620",
        "account_name": "Depreciation - General Pool",
        "classification": "Depreciation - General Pool (SBE Div 328)",
        "section": "expenses",
        "tax_code": "",
        "display_order": 1620,
    },
    {
        "account_code": "2905",
        "account_name": "General Pool",
        "classification": "General Pool - Carrying Value",
        "section": "non_current_assets",
        "tax_code": "",
        "display_order": 2905,
    },
]


class Command(BaseCommand):
    help = "Seed general pool CoA accounts (1620, 2905) into master templates and all entities."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply changes (default is dry-run preview only)",
        )

    def handle(self, *args, **options):
        apply = options["apply"]
        mode = "APPLY" if apply else "DRY-RUN"
        self.stdout.write(self.style.WARNING(f"\n[{mode}] seed_general_pool_coa\n"))

        # ── 1. Master ChartOfAccount template ──────────────────────────────
        self.stdout.write("── Master ChartOfAccount template ──")
        master_created = 0
        master_updated = 0
        for etype in ENTITY_TYPES:
            for acct in NEW_ACCOUNTS:
                existing = ChartOfAccount.objects.filter(
                    entity_type=etype, account_code=acct["account_code"]
                ).first()
                if existing:
                    if (
                        existing.account_name != acct["account_name"]
                        or existing.section != acct["section"]
                    ):
                        self.stdout.write(
                            f"  UPDATE  {etype} / {acct['account_code']} "
                            f"'{existing.account_name}' → '{acct['account_name']}'"
                        )
                        if apply:
                            existing.account_name = acct["account_name"]
                            existing.section = acct["section"]
                            existing.classification = acct["classification"]
                            existing.is_active = True
                            existing.save()
                        master_updated += 1
                    else:
                        self.stdout.write(
                            f"  OK      {etype} / {acct['account_code']} already correct"
                        )
                else:
                    self.stdout.write(
                        f"  CREATE  {etype} / {acct['account_code']} '{acct['account_name']}'"
                    )
                    if apply:
                        ChartOfAccount.objects.create(
                            entity_type=etype,
                            account_code=acct["account_code"],
                            account_name=acct["account_name"],
                            classification=acct["classification"],
                            section=acct["section"],
                            tax_code=acct["tax_code"],
                            display_order=acct["display_order"],
                            is_active=True,
                        )
                    master_created += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"  Master: {master_created} to create, {master_updated} to update\n"
            )
        )

        # ── 2. EntityChartOfAccount (per-entity) ───────────────────────────
        self.stdout.write("── EntityChartOfAccount (per entity) ──")
        entity_created = 0
        entity_skipped = 0
        for entity in Entity.objects.all().order_by("entity_name"):
            for acct in NEW_ACCOUNTS:
                existing = EntityChartOfAccount.objects.filter(
                    entity=entity, account_code=acct["account_code"]
                ).first()
                if existing:
                    entity_skipped += 1
                else:
                    self.stdout.write(
                        f"  CREATE  {entity.entity_name} ({entity.entity_type}) "
                        f"/ {acct['account_code']} '{acct['account_name']}'"
                    )
                    if apply:
                        EntityChartOfAccount.objects.create(
                            entity=entity,
                            account_code=acct["account_code"],
                            account_name=acct["account_name"],
                            classification=acct["classification"],
                            section=acct["section"],
                            tax_code=acct["tax_code"],
                            display_order=acct["display_order"],
                            is_active=True,
                        )
                    entity_created += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"  Entities: {entity_created} to create, {entity_skipped} already present\n"
            )
        )

        if not apply:
            self.stdout.write(
                self.style.WARNING(
                    "Dry-run complete. Run with --apply to commit changes."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "Done. General pool accounts seeded successfully."
                )
            )
