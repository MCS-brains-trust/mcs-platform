"""
Management command to strip leading zeros from revenue account codes
across all existing entities in the platform.

HandiLedger uses 4-digit codes like 0575 for revenue accounts (0-999),
but StatementHub uses 575 (no leading zero). This command fixes all
existing records that have the leading zero.

Affected models:
- TrialBalanceLine.account_code
- ClientAccountMapping.client_account_code
- EntityChartOfAccount.account_code

Usage:
    python manage.py fix_leading_zero_codes          # Dry run (preview)
    python manage.py fix_leading_zero_codes --apply   # Apply changes
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import TrialBalanceLine, ClientAccountMapping, EntityChartOfAccount


def _needs_fix(code):
    """Check if an account code has a leading zero that should be stripped."""
    if not code:
        return False
    base = code.split('.')[0]
    return (
        base.startswith('0')
        and base.isdigit()
        and len(base) > 1
    )


def _fix_code(code):
    """Strip leading zero(s) from an account code."""
    parts = code.split('.', 1)
    parts[0] = parts[0].lstrip('0') or '0'
    return '.'.join(parts)


class Command(BaseCommand):
    help = 'Strip leading zeros from revenue account codes (HandiLedger 0575 -> 575)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Actually apply the changes (default is dry-run)',
        )

    def handle(self, *args, **options):
        apply = options['apply']
        mode = "APPLYING" if apply else "DRY RUN"
        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  Fix Leading Zero Account Codes — {mode}")
        self.stdout.write(f"{'='*60}\n")

        # 1. TrialBalanceLine
        tb_lines = TrialBalanceLine.objects.all()
        tb_count = 0
        tb_conflicts = 0
        for tbl in tb_lines.iterator():
            if _needs_fix(tbl.account_code):
                new_code = _fix_code(tbl.account_code)
                # Check for conflict: does a line with the new code already
                # exist in the same financial year?
                conflict = TrialBalanceLine.objects.filter(
                    financial_year=tbl.financial_year,
                    account_code=new_code,
                ).exclude(pk=tbl.pk).exists()
                if conflict:
                    tb_conflicts += 1
                    self.stdout.write(
                        self.style.WARNING(
                            f"  CONFLICT: TB line {tbl.account_code} -> {new_code} "
                            f"in FY {tbl.financial_year} (skipped)"
                        )
                    )
                    continue
                self.stdout.write(
                    f"  TB: {tbl.account_code} -> {new_code} "
                    f"({tbl.account_name}, FY: {tbl.financial_year})"
                )
                if apply:
                    tbl.account_code = new_code
                    tbl.save(update_fields=['account_code'])
                tb_count += 1

        # 2. ClientAccountMapping
        cam_qs = ClientAccountMapping.objects.all()
        cam_count = 0
        for cam in cam_qs.iterator():
            if _needs_fix(cam.client_account_code):
                new_code = _fix_code(cam.client_account_code)
                # Check for conflict
                conflict = ClientAccountMapping.objects.filter(
                    entity=cam.entity,
                    client_account_code=new_code,
                ).exclude(pk=cam.pk).exists()
                if conflict:
                    self.stdout.write(
                        self.style.WARNING(
                            f"  CONFLICT: CAM {cam.client_account_code} -> {new_code} "
                            f"for entity {cam.entity} (skipped)"
                        )
                    )
                    continue
                self.stdout.write(
                    f"  CAM: {cam.client_account_code} -> {new_code} "
                    f"(entity: {cam.entity})"
                )
                if apply:
                    cam.client_account_code = new_code
                    cam.save(update_fields=['client_account_code'])
                cam_count += 1

        # 3. EntityChartOfAccount
        eca_qs = EntityChartOfAccount.objects.all()
        eca_count = 0
        for eca in eca_qs.iterator():
            if _needs_fix(eca.account_code):
                new_code = _fix_code(eca.account_code)
                # Check for conflict
                conflict = EntityChartOfAccount.objects.filter(
                    entity=eca.entity,
                    account_code=new_code,
                ).exclude(pk=eca.pk).exists()
                if conflict:
                    self.stdout.write(
                        self.style.WARNING(
                            f"  CONFLICT: ECA {eca.account_code} -> {new_code} "
                            f"for entity {eca.entity} (skipped)"
                        )
                    )
                    continue
                self.stdout.write(
                    f"  ECA: {eca.account_code} -> {new_code} "
                    f"(entity: {eca.entity}, name: {eca.account_name})"
                )
                if apply:
                    eca.account_code = new_code
                    eca.save(update_fields=['account_code'])
                eca_count += 1

        # Summary
        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"  Summary ({mode}):")
        self.stdout.write(f"    TrialBalanceLine:       {tb_count} updated ({tb_conflicts} conflicts skipped)")
        self.stdout.write(f"    ClientAccountMapping:   {cam_count} updated")
        self.stdout.write(f"    EntityChartOfAccount:   {eca_count} updated")
        self.stdout.write(f"{'='*60}\n")

        if not apply:
            self.stdout.write(
                self.style.NOTICE(
                    "  This was a DRY RUN. To apply changes, run with --apply"
                )
            )
