"""
Data migration: mark all existing confirmed PendingTransactions as
already posted to the trial balance so the new guard does not re-post them.
"""
from django.db import migrations


def backfill_posted_to_tb(apps, schema_editor):
    PendingTransaction = apps.get_model('review', 'PendingTransaction')
    PendingTransaction.objects.filter(
        is_confirmed=True,
    ).update(posted_to_tb=True)


def reverse_backfill(apps, schema_editor):
    PendingTransaction = apps.get_model('review', 'PendingTransaction')
    PendingTransaction.objects.all().update(posted_to_tb=False)


class Migration(migrations.Migration):

    dependencies = [
        ('review', '0008_pendingtransaction_posted_to_tb'),
    ]

    operations = [
        migrations.RunPython(backfill_posted_to_tb, reverse_backfill),
    ]
