"""
Merge migration to resolve conflict between:
- 0007_alter_entitygstsetting_financial_year (created directly on server)
- 0009_backfill_posted_to_tb (in repo)

The server's 0007 migration altered EntityGSTSetting.financial_year.
The repo's chain is 0007_merge -> 0008 -> 0009.
This merge migration makes both leaf nodes converge.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        # The repo's leaf node
        ('review', '0009_backfill_posted_to_tb'),
    ]

    operations = [
        # No operations needed — this is purely a merge migration
    ]
