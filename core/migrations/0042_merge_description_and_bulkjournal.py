"""
Merge migration to resolve the conflict between:
- 0040_add_description_to_trialbalanceline (repo: description field)
- 0041_alter_trialbalanceline_source_bulkjournalupload_and_more (server: bulk journal upload)

Both branched from 0039_add_reopen_fields via different intermediate paths.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0040_add_description_to_trialbalanceline"),
        ("core", "0041_alter_trialbalanceline_source_bulkjournalupload_and_more"),
    ]

    operations = [
        # No operations needed — this just merges the two branches
    ]
