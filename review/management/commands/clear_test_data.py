"""Management command to clear all test bank statement review data."""
from django.db import models as db_models
from django.core.management.base import BaseCommand
from review.models import ReviewJob, PendingTransaction, TransactionPattern, ReviewActivity


class Command(BaseCommand):
    help = 'Clear all test bank statement review data (ReviewJobs, PendingTransactions, TransactionPatterns, ReviewActivity)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--user',
            type=str,
            default=None,
            help='Filter by submitted_by or client_name containing this value (optional - if omitted, clears ALL data)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without actually deleting',
        )

    def handle(self, *args, **options):
        user_filter = options.get('user')
        dry_run = options.get('dry_run', False)

        if user_filter:
            jobs_qs = ReviewJob.objects.filter(
                db_models.Q(submitted_by__icontains=user_filter) |
                db_models.Q(client_name__icontains=user_filter)
            )
            self.stdout.write(f'Filtering by user: "{user_filter}"')
        else:
            jobs_qs = ReviewJob.objects.all()
            self.stdout.write('No --user filter: targeting ALL data')

        # Count before deletion
        jobs = jobs_qs.count()
        job_ids = list(jobs_qs.values_list('id', flat=True))

        transactions_qs = PendingTransaction.objects.filter(job_id__in=job_ids)
        transactions = transactions_qs.count()

        activities_qs = ReviewActivity.objects.all() if not user_filter else ReviewActivity.objects.filter(
            db_models.Q(title__icontains=user_filter) |
            db_models.Q(description__icontains=user_filter)
        )
        activities = activities_qs.count()

        if user_filter:
            entity_ids = jobs_qs.exclude(entity__isnull=True).values_list('entity_id', flat=True)
            patterns_qs = TransactionPattern.objects.filter(entity_id__in=entity_ids)
        else:
            patterns_qs = TransactionPattern.objects.all()
        patterns = patterns_qs.count()

        self.stdout.write(
            f'Found: {jobs} ReviewJobs, {transactions} PendingTransactions, '
            f'{patterns} TransactionPatterns, {activities} ReviewActivities'
        )

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN - no data was deleted.'))
            return

        if jobs == 0 and transactions == 0 and patterns == 0 and activities == 0:
            self.stdout.write(self.style.SUCCESS('Nothing to delete - database is already clean.'))
            return

        # Delete in order (transactions first due to FK)
        transactions_qs.delete()
        activities_qs.delete()
        patterns_qs.delete()
        jobs_qs.delete()

        self.stdout.write(self.style.SUCCESS(
            f'Deleted: {jobs} ReviewJobs, {transactions} PendingTransactions, '
            f'{patterns} TransactionPatterns, {activities} ReviewActivities'
        ))
        self.stdout.write(self.style.SUCCESS('Test bank statement data has been cleared.'))
