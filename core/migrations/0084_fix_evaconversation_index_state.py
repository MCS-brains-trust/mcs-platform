from django.db import migrations


class Migration(migrations.Migration):
    """Sync Django's migration state for the EvaConversation index.

    Migration 0047 was emptied to fix SQLite compat, and the RenameIndex was
    also removed from 0081.  The production DB already has the index under
    its auto-generated name (or not at all).  We use SeparateDatabaseAndState
    to tell Django the rename happened without touching the actual database.
    """

    dependencies = [
        ("core", "0083_add_unique_finding_key_constraint"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RenameIndex(
                    model_name="evaconversation",
                    new_name="core_evacon_financi_652320_idx",
                    old_name="core_evaconvo_fy_user_idx",
                ),
            ],
            database_operations=[],
        ),
    ]
