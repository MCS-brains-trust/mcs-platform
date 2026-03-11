"""
Data migration: Consolidate FinancialYear statuses to the new 4-state workflow.

New workflow: draft → in_review → finalised → reopened → in_review

Mapping:
  - finished     → in_review  (accountant will need to re-run Eva)
  - prepared     → in_review
  - pending_eva  → in_review
  - eva_cleared  → in_review
  - eva_error    → in_review
  - locked       → finalised
  - draft        → draft       (unchanged)
  - in_review    → in_review   (unchanged)
  - finalised    → finalised   (unchanged — promoted from legacy)
  - reopened     → reopened    (unchanged)
"""
from django.db import migrations


# Map old status values → new status values
STATUS_MAP = {
    'finished': 'in_review',
    'prepared': 'in_review',
    'pending_eva': 'in_review',
    'eva_cleared': 'in_review',
    'eva_error': 'in_review',
    'locked': 'finalised',
}


def migrate_statuses_forward(apps, schema_editor):
    FinancialYear = apps.get_model('core', 'FinancialYear')
    for old_status, new_status in STATUS_MAP.items():
        updated = FinancialYear.objects.filter(status=old_status).update(status=new_status)
        if updated:
            print(f"  Migrated {updated} FY(s) from '{old_status}' → '{new_status}'")


def migrate_statuses_reverse(apps, schema_editor):
    # Best-effort reverse: in_review → in_review (no-op), finalised → locked
    FinancialYear = apps.get_model('core', 'FinancialYear')
    # We can only meaningfully reverse locked→finalised
    FinancialYear.objects.filter(status='finalised').update(status='locked')


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0076_evafindingsuppression"),
    ]

    operations = [
        migrations.RunPython(
            migrate_statuses_forward,
            migrate_statuses_reverse,
        ),
    ]
