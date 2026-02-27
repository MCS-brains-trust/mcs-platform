"""
Custom migration to rename FinancialYear status 'reviewed' → 'finished'.

This updates:
1. The status field choices (via schema migration)
2. Any existing rows with status='reviewed' → status='finished' (via data migration)
"""
from django.db import migrations, models


def rename_reviewed_to_finished(apps, schema_editor):
    """Update all FinancialYear records with status='reviewed' to 'finished'."""
    FinancialYear = apps.get_model("core", "FinancialYear")
    updated = FinancialYear.objects.filter(status="reviewed").update(status="finished")
    if updated:
        print(f"\n  → Updated {updated} FinancialYear record(s) from 'reviewed' to 'finished'")


def rename_finished_to_reviewed(apps, schema_editor):
    """Reverse: rename 'finished' back to 'reviewed'."""
    FinancialYear = apps.get_model("core", "FinancialYear")
    updated = FinancialYear.objects.filter(status="finished").update(status="reviewed")
    if updated:
        print(f"\n  → Reverted {updated} FinancialYear record(s) from 'finished' to 'reviewed'")


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0045_eva_v2_knowledge_brain_chat"),
    ]

    operations = [
        # Step 1: Update the field choices to include 'finished' and remove 'reviewed'
        migrations.AlterField(
            model_name="financialyear",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "Draft"),
                    ("in_review", "In Review"),
                    ("finished", "Finished"),
                    ("prepared", "Prepared (Eva Reviewing)"),
                    ("pending_eva", "Pending Eva Review"),
                    ("eva_cleared", "Eva Cleared"),
                    ("eva_error", "Eva Error"),
                    ("finalised", "Finalised"),
                ],
                default="draft",
                max_length=20,
            ),
        ),
        # Step 2: Rename existing data
        migrations.RunPython(
            rename_reviewed_to_finished,
            rename_finished_to_reviewed,
        ),
    ]
