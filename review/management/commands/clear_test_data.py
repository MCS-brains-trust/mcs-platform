"""Management command to clear all test bank statement review data."""
from django.core.management.base import BaseCommand
from review.models import ReviewJob, PendingTransaction, TransactionPattern, ReviewActivity


class Command(BaseCommand):
    help = 'Clear all test bank statement review data (ReviewJobs, PendingTransactions, TransactionPatterns, ReviewActivity)'

    def handle(self, *args, **options):
        # Count before deletion
        jobs = ReviewJob.objects.count()
        transactions = PendingTransaction.objects.count()
        patterns = TransactionPattern.objects.count()
        activities = ReviewActivity.objects.count()

        self.stdout.write(f'Found: {jobs} ReviewJobs, {transactions} PendingTransactions, '
                          f'{patterns} TransactionPatterns, {activities} ReviewActivities')

        # Delete in order (transactions first due to FK)
        PendingTransaction.objects.all().delete()
        ReviewActivity.objects.all().delete()
        TransactionPattern.objects.all().delete()
        ReviewJob.objects.all().delete()

        self.stdout.write(self.style.SUCCESS(
            f'Deleted: {jobs} ReviewJobs, {transactions} PendingTransactions, '
            f'{patterns} TransactionPatterns, {activities} ReviewActivities'
        ))
        self.stdout.write(self.style.SUCCESS('All test bank statement data has been cleared.'))
