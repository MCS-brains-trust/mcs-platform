"""
Management command: backfill_dep_assets

Retroactively populates the depreciation schedule for financial years that
have non-current asset accounts on the trial balance but no depreciation
assets in the database.

This fixes entities that were imported from Handiledger ZIPs that did not
include DepSchedule.txt / AssetMaster.txt / AssetDetail.txt files.

Usage:
    python manage.py backfill_dep_assets
    python manage.py backfill_dep_assets --entity-name "Eva"
    python manage.py backfill_dep_assets --dry-run
"""
from decimal import Decimal

from django.core.management.base import BaseCommand

from core.models import DepreciationAsset, Entity, FinancialYear, TrialBalanceLine
from core.access_ledger_import import _derive_depreciation_from_tb


class Command(BaseCommand):
    help = "Backfill depreciation assets from TB for years with no dep schedule data"

    def add_arguments(self, parser):
        parser.add_argument(
            "--entity-name",
            type=str,
            default=None,
            help="Only process entities whose name contains this string (case-insensitive)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Show what would be created without saving anything",
        )

    def handle(self, *args, **options):
        entity_filter = options.get("entity_name")
        dry_run = options.get("dry_run")

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no changes will be saved"))

        entities = Entity.objects.all()
        if entity_filter:
            entities = entities.filter(entity_name__icontains=entity_filter)

        total_created = 0
        total_years = 0

        for entity in entities:
            for fy in entity.financial_years.all().order_by("year_label"):
                # Skip years that already have depreciation assets
                if fy.depreciation_assets.exists():
                    continue

                # Get all TB lines for this year
                tb_lines_qs = TrialBalanceLine.objects.filter(
                    financial_year=fy
                ).values("account_code", "account_name", "closing_balance")

                # Convert to list of dicts for _derive_depreciation_from_tb
                tb_lines = [
                    {
                        "account_code": row["account_code"],
                        "account_name": row["account_name"],
                        "closing_balance": row["closing_balance"] or Decimal("0"),
                    }
                    for row in tb_lines_qs
                ]

                if not tb_lines:
                    continue

                derived = _derive_depreciation_from_tb(tb_lines)
                if not derived:
                    continue

                self.stdout.write(
                    f"  {entity.entity_name} / {fy.year_label}: "
                    f"would create {len(derived)} asset(s) from TB"
                    if dry_run else
                    f"  {entity.entity_name} / {fy.year_label}: "
                    f"creating {len(derived)} asset(s) from TB"
                )

                if not dry_run:
                    for asset_data in derived:
                        DepreciationAsset.objects.create(
                            financial_year=fy,
                            category=asset_data["category"],
                            asset_name=asset_data["asset_name"],
                            purchase_date=asset_data["purchase_date"],
                            total_cost=asset_data["total_cost"],
                            private_use_pct=asset_data["private_use_pct"],
                            opening_wdv=asset_data["opening_wdv"],
                            disposal_date=asset_data["disposal_date"],
                            disposal_consideration=asset_data["disposal_consideration"],
                            addition_date=asset_data["addition_date"],
                            addition_cost=asset_data["addition_cost"],
                            depreciable_value=asset_data["depreciable_value"],
                            method=asset_data["method"],
                            rate=asset_data["rate"],
                            depreciation_amount=asset_data["depreciation_amount"],
                            private_depreciation=asset_data["private_depreciation"],
                            closing_wdv=asset_data["closing_wdv"],
                            display_order=asset_data["display_order"],
                            notes="Derived from TB (no DepSchedule.txt in ZIP)",
                        )

                total_created += len(derived)
                total_years += 1

        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f"\nDRY RUN complete: would create {total_created} asset(s) "
                    f"across {total_years} year(s)"
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"\nDone: created {total_created} asset(s) "
                    f"across {total_years} year(s)"
                )
            )
