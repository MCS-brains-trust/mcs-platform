"""
Management command to recalculate gst_amount and net_amount for existing
PendingTransactions where gst_treatment is 'taxable' (or empty on a
GST-registered job) but gst_amount is still 0.

Usage:
    python3 manage.py recalc_gst          # dry-run (default)
    python3 manage.py recalc_gst --apply  # actually update rows
"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Q

from review.models import PendingTransaction


class Command(BaseCommand):
    help = "Recalculate GST for transactions with gst_amount=0 on GST-registered jobs"

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply", action="store_true",
            help="Actually update the database (default is dry-run)",
        )

    def handle(self, *args, **options):
        apply = options["apply"]

        # Find transactions on GST-registered jobs where GST should be
        # calculated but gst_amount is still 0
        qs = PendingTransaction.objects.filter(
            job__is_gst_registered=True,
            gst_amount=Decimal("0.00"),
            gst_amount_override__isnull=True,  # don't touch overrides
            posted_to_tb=False,                # don't touch posted rows
        ).filter(
            Q(gst_treatment="taxable") | Q(gst_treatment="")
        )

        count = qs.count()
        self.stdout.write(f"Found {count} transactions to recalculate")

        if not count:
            return

        updated = 0
        for txn in qs.iterator():
            abs_amount = abs(txn.amount)
            sign = Decimal("-1") if txn.amount < 0 else Decimal("1")
            cred_pct = txn.creditable_percentage or Decimal("100")
            full_gst = (abs_amount / Decimal("11")).quantize(Decimal("0.01"))
            gst_amt = (full_gst * cred_pct / Decimal("100")).quantize(Decimal("0.01")) * sign
            net_amt = (abs_amount - full_gst).quantize(Decimal("0.01")) * sign

            if apply:
                txn.gst_amount = gst_amt
                txn.net_amount = net_amt
                txn.save(update_fields=["gst_amount", "net_amount", "updated_at"])

            self.stdout.write(
                f"  {'UPDATED' if apply else 'WOULD UPDATE'}: "
                f"{txn.date} {txn.description[:40]} "
                f"gross=${txn.amount} -> gst=${gst_amt} net=${net_amt}"
            )
            updated += 1

        action = "Updated" if apply else "Would update"
        self.stdout.write(self.style.SUCCESS(f"\n{action} {updated} transactions"))
        if not apply:
            self.stdout.write("Run with --apply to actually update the database")
