"""
One-off management command to fix corrupted Trial Balance lines for
CST Automation Pty Ltd (FY 2025).

The previous buggy journal netting code corrupted the original import
rows for accounts 2001, 2002, and 2101. This command:
1. Deletes ALL is_adjustment=True TB lines for this financial year
2. Restores the correct debit values for the corrupted accounts

Usage:
    python manage.py fix_cst_tb
    python manage.py fix_cst_tb --dry-run
"""
from decimal import Decimal
from django.core.management.base import BaseCommand
from core.models import TrialBalanceLine, FinancialYear


# Financial year UUID for CST Automation 2025
FY_UUID = 'bf616842-a8b4-4cd3-9920-01c1a0dc5cf1'

# Correct values for the corrupted accounts (original import values)
CORRECTIONS = {
    '2001': {'debit': Decimal('627808.45'), 'credit': Decimal('0')},
    '2002': {'debit': Decimal('71791.90'), 'credit': Decimal('0')},
    '2101': {'debit': Decimal('137502.20'), 'credit': Decimal('0')},
}


class Command(BaseCommand):
    help = 'Fix corrupted TB lines for CST Automation Pty Ltd (FY 2025)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Show what would be changed without making changes',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        try:
            fy = FinancialYear.objects.get(pk=FY_UUID)
        except FinancialYear.DoesNotExist:
            self.stderr.write(self.style.ERROR(
                f'Financial year {FY_UUID} not found.'
            ))
            return

        self.stdout.write(f'Entity: {fy.entity.entity_name}')
        self.stdout.write(f'Financial Year: {fy.year_label}')
        self.stdout.write('')

        # Step 1: Delete all adjustment rows
        adj_lines = TrialBalanceLine.objects.filter(
            financial_year=fy, is_adjustment=True
        )
        adj_count = adj_lines.count()
        self.stdout.write(f'Found {adj_count} adjustment TB lines to delete:')
        for line in adj_lines:
            self.stdout.write(
                f'  {line.account_code} {line.account_name} '
                f'Dr={line.debit} Cr={line.credit} (is_adjustment=True)'
            )

        if not dry_run and adj_count > 0:
            adj_lines.delete()
            self.stdout.write(self.style.SUCCESS(
                f'Deleted {adj_count} adjustment lines.'
            ))

        # Step 2: Restore correct values for corrupted accounts
        self.stdout.write('')
        self.stdout.write('Checking and restoring corrupted accounts:')
        for code, correct in CORRECTIONS.items():
            lines = TrialBalanceLine.objects.filter(
                financial_year=fy,
                account_code=code,
                is_adjustment=False,
            )
            if lines.count() == 0:
                self.stdout.write(self.style.WARNING(
                    f'  {code}: No import line found — skipping'
                ))
                continue

            line = lines.first()
            current_dr = line.debit
            current_cr = line.credit
            target_dr = correct['debit']
            target_cr = correct['credit']

            if current_dr == target_dr and current_cr == target_cr:
                self.stdout.write(self.style.SUCCESS(
                    f'  {code} {line.account_name}: Already correct '
                    f'(Dr={current_dr}, Cr={current_cr})'
                ))
            else:
                self.stdout.write(
                    f'  {code} {line.account_name}: '
                    f'CORRUPTED Dr={current_dr} Cr={current_cr} '
                    f'→ RESTORING Dr={target_dr} Cr={target_cr}'
                )
                if not dry_run:
                    line.debit = target_dr
                    line.credit = target_cr
                    line.closing_balance = target_dr - target_cr
                    line.save(update_fields=['debit', 'credit', 'closing_balance'])
                    self.stdout.write(self.style.SUCCESS(f'    Restored.'))

        if dry_run:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING(
                'DRY RUN — no changes were made. '
                'Run without --dry-run to apply fixes.'
            ))
        else:
            self.stdout.write('')
            self.stdout.write(self.style.SUCCESS('All fixes applied successfully.'))
