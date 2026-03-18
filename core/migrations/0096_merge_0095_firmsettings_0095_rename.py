"""
Migration: 0096_merge_0095_firmsettings_0095_rename

Merge migration to resolve the conflict between:
  - 0095_firmsettings  (added in this branch — FirmSettings model)
  - 0095_rename_core_govdocc_entity__7f0b6f_idx_core_govern_entity__20bbbb_idx_and_more
    (existed on the production server, applied outside of git history)

This merge migration has no operations of its own; it simply declares
both leaf nodes as its dependencies so Django can proceed.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0095_firmsettings"),
        ("core", "0095_rename_core_govdocc_entity__7f0b6f_idx_core_govern_entity__20bbbb_idx_and_more"),
    ]

    operations = [
    ]
