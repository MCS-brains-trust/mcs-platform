"""
One-off management command to fix the $3,960 TB imbalance for
Cos Me Pty Ltd (FY 2026).

The imbalance was caused by 6 GoCardless direct debit payments of $660
each (account 1930 — Software development) that were approved via
review_bulk_approve_group, which was missing the _post_bank_contra_entry
call. The expense + GST debits were posted but the bank credit was not.

This command uses the existing recalculate_bank_contra_entries logic:
it compares the expected bank contra total (sum of all confirmed txn
gross amounts) against the actual bank TB line, and posts the shortfall.

Usage:
    python3 manage.py fix_cosme_contra --dry-run
    python3 manage.py fix_cosme_contra
"""
from decimal import Decimal

from django.core.management.base import BaseCommand

from core.models import (
    Entity,
    FinancialYear,
    TrialBalanceLine,
    BankAccountMapping,
)
from review.models import PendingTransaction


class Command(BaseCommand):
    help = "Fix missing bank contra entries for Cos Me Pty Ltd FY2026"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be changed without modifying the database.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        # ── 1. Find the entity ──────────────────────────────────────────
        entity = Entity.objects.filter(name__icontains="Cos Me").first()
        if not entity:
            self.stderr.write(self.style.ERROR(
                "Could not find an entity matching 'Cos Me'. "
                "Available entities:"
            ))
            for e in Entity.objects.all().order_by("name"):
                self.stderr.write(f"  - {e.name} ({e.pk})")
            return

        self.stdout.write(f"Entity: {entity.name} ({entity.pk})")

        # ── 2. Find the FY ending 2026 ─────────────────────────────────
        fy = FinancialYear.objects.filter(
            entity=entity,
            end_date__year=2026,
        ).first()
        if not fy:
            # Fallback: most recent FY
            fy = FinancialYear.objects.filter(
                entity=entity,
            ).order_by("-end_date").first()
        if not fy:
            self.stderr.write(self.style.ERROR(
                f"No financial year found for {entity.name}."
            ))
            return

        self.stdout.write(f"Financial Year: {fy} (pk={fy.pk})")
        self.stdout.write(f"  Period: {fy.start_date} to {fy.end_date}")

        # ── 3. Show current TB totals ───────────────────────────────────
        all_tb = TrialBalanceLine.objects.filter(
            financial_year=fy, is_adjustment=False,
        )
        total_dr = sum((l.debit or Decimal("0")) for l in all_tb)
        total_cr = sum((l.credit or Decimal("0")) for l in all_tb)
        diff = total_dr - total_cr
        self.stdout.write(f"\nCurrent TB totals:")
        self.stdout.write(f"  Total Dr: ${total_dr:,.2f}")
        self.stdout.write(f"  Total Cr: ${total_cr:,.2f}")
        self.stdout.write(f"  Difference: ${diff:,.2f}")

        if diff == Decimal("0"):
            self.stdout.write(self.style.SUCCESS(
                "\nTB is already balanced. No fix needed."
            ))
            return

        # ── 4. Find the bank account mapping ────────────────────────────
        confirmed_txns = PendingTransaction.objects.filter(
            job__entity=entity,
            is_confirmed=True,
        ).select_related("job")

        self.stdout.write(f"\nConfirmed transactions: {confirmed_txns.count()}")

        if not confirmed_txns.exists():
            self.stderr.write(self.style.ERROR("No confirmed transactions found."))
            return

        # Resolve bank mapping using the same lookup chain as the app
        sample_txn = confirmed_txns.first()
        job = sample_txn.job
        bank_mapping = None

        # 1. Exact match
        if job and (job.bsb or job.account_number):
            bank_mapping = BankAccountMapping.objects.filter(
                entity=entity, bsb=job.bsb or "", account_number=job.account_number or "",
            ).first()
        # 2. Default
        if not bank_mapping:
            bank_mapping = BankAccountMapping.objects.filter(
                entity=entity, is_default=True,
            ).first()
        # 3. Catch-all
        if not bank_mapping:
            bank_mapping = BankAccountMapping.objects.filter(
                entity=entity, bsb="", account_number="",
            ).first()
        # 4. Only one
        if not bank_mapping:
            qs = BankAccountMapping.objects.filter(entity=entity)
            if qs.count() == 1:
                bank_mapping = qs.first()
        # 5. Any
        if not bank_mapping:
            bank_mapping = BankAccountMapping.objects.filter(
                entity=entity,
            ).order_by("-updated_at").first()

        if not bank_mapping:
            self.stderr.write(self.style.ERROR(
                "No bank account mapping found for this entity. "
                "Cannot determine which TB line to credit."
            ))
            return

        bank_code = bank_mapping.tb_account_code
        bank_name = bank_mapping.tb_account_name
        self.stdout.write(f"Bank mapping: {bank_code} — {bank_name}")

        # ── 5. Calculate expected vs actual bank contra ─────────────────
        expected_debit = Decimal("0")
        expected_credit = Decimal("0")
        for txn in confirmed_txns:
            gross = abs(txn.amount)
            if txn.amount > 0:
                expected_debit += gross
            elif txn.amount < 0:
                expected_credit += gross

        self.stdout.write(f"\nExpected bank contra (from {confirmed_txns.count()} txns):")
        self.stdout.write(f"  Expected Dr (receipts):  ${expected_debit:,.2f}")
        self.stdout.write(f"  Expected Cr (payments):  ${expected_credit:,.2f}")

        bank_tb_lines = TrialBalanceLine.objects.filter(
            financial_year=fy,
            account_code=bank_code,
            is_adjustment=False,
        )
        current_debit = Decimal("0")
        current_credit = Decimal("0")
        for bl in bank_tb_lines:
            current_debit += bl.debit or Decimal("0")
            current_credit += bl.credit or Decimal("0")

        self.stdout.write(f"\nActual bank TB line ({bank_code}):")
        self.stdout.write(f"  Current Dr:  ${current_debit:,.2f}")
        self.stdout.write(f"  Current Cr:  ${current_credit:,.2f}")

        missing_debit = expected_debit - current_debit
        missing_credit = expected_credit - current_credit

        self.stdout.write(f"\nShortfall:")
        self.stdout.write(f"  Missing Dr:  ${missing_debit:,.2f}")
        self.stdout.write(f"  Missing Cr:  ${missing_credit:,.2f}")

        if missing_debit == 0 and missing_credit == 0:
            self.stdout.write(self.style.SUCCESS(
                "\nBank contra entries are already correct. No fix needed."
            ))
            return

        # ── 6. Identify the specific missing transactions ───────────────
        self.stdout.write(f"\nIdentifying transactions without bank contra...")
        # The 6 GoCardless txns to 1930 are the known culprits
        gocardless_txns = confirmed_txns.filter(
            confirmed_code="1930",
            description__icontains="GoCardless",
        )
        if gocardless_txns.exists():
            self.stdout.write(f"\nGoCardless transactions to account 1930:")
            for txn in gocardless_txns:
                self.stdout.write(
                    f"  {txn.date}  {txn.description[:50]:<50}  "
                    f"${txn.amount:>10,.2f}  code={txn.confirmed_code}"
                )
            self.stdout.write(
                f"  Total: ${sum(t.amount for t in gocardless_txns):,.2f} "
                f"({gocardless_txns.count()} transactions)"
            )

        # ── 7. Apply the fix ────────────────────────────────────────────
        if dry_run:
            self.stdout.write(self.style.WARNING(
                f"\n[DRY RUN] Would post missing bank contra: "
                f"Dr ${missing_debit:,.2f} / Cr ${missing_credit:,.2f} "
                f"to {bank_code} ({bank_name})"
            ))
            # Show what the TB would look like after
            new_dr = total_dr + missing_debit
            new_cr = total_cr + missing_credit
            new_diff = new_dr - new_cr
            self.stdout.write(f"\nProjected TB after fix:")
            self.stdout.write(f"  Total Dr: ${new_dr:,.2f}")
            self.stdout.write(f"  Total Cr: ${new_cr:,.2f}")
            self.stdout.write(f"  Difference: ${new_diff:,.2f}")
            if new_diff == Decimal("0"):
                self.stdout.write(self.style.SUCCESS("  ✓ TB would be balanced"))
            else:
                self.stdout.write(self.style.WARNING(
                    f"  ⚠ TB would still be out by ${new_diff:,.2f}"
                ))
            return

        # Get or create the bank TB line
        tb_line = bank_tb_lines.first()
        if not tb_line:
            tb_line = TrialBalanceLine.objects.create(
                financial_year=fy,
                account_code=bank_code,
                account_name=bank_name,
                debit=max(Decimal("0"), missing_debit),
                credit=max(Decimal("0"), missing_credit),
                closing_balance=missing_debit - missing_credit,
                tax_type="",
                source="bank_statement",
            )
            self.stdout.write(f"\nCreated new TB line for {bank_code}")
        else:
            if missing_debit > 0:
                tb_line.debit += missing_debit
                tb_line.closing_balance += missing_debit
            if missing_credit > 0:
                tb_line.credit += missing_credit
                tb_line.closing_balance -= missing_credit
            if not tb_line.source:
                tb_line.source = "bank_statement"
            tb_line.save()
            self.stdout.write(f"\nUpdated TB line for {bank_code}")

        # ── 8. Verify ──────────────────────────────────────────────────
        all_tb = TrialBalanceLine.objects.filter(
            financial_year=fy, is_adjustment=False,
        )
        new_dr = sum((l.debit or Decimal("0")) for l in all_tb)
        new_cr = sum((l.credit or Decimal("0")) for l in all_tb)
        new_diff = new_dr - new_cr

        self.stdout.write(f"\nTB after fix:")
        self.stdout.write(f"  Total Dr: ${new_dr:,.2f}")
        self.stdout.write(f"  Total Cr: ${new_cr:,.2f}")
        self.stdout.write(f"  Difference: ${new_diff:,.2f}")

        if new_diff == Decimal("0"):
            self.stdout.write(self.style.SUCCESS(
                "\n✓ TB is now balanced. Fix applied successfully."
            ))
        else:
            self.stdout.write(self.style.WARNING(
                f"\n⚠ TB is still out by ${new_diff:,.2f}. "
                f"Manual investigation may be needed."
            ))
