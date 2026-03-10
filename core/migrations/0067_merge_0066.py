"""
Merge migration to resolve conflict between:
- 0066_depreciationasset_notes (created directly on server)
- 0066_evaclarification (in repo)

Both depend on 0065_workpaper_template. This no-op merge migration
makes both leaf nodes converge so migrations can proceed.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0066_depreciationasset_notes'),
        ('core', '0066_evaclarification'),
    ]

    operations = [
        # No operations needed — this is purely a merge migration
    ]
