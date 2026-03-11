"""
Extend ActivityLog model:
- Add eva_finding FK, metadata JSONField
- New EventType choices (TB_IMPORT_DUPLICATE_MERGED, FY_STATUS_CHANGED,
  EVA_REVIEW_TRIGGERED, EVA_REVIEW_CLEARED, EVA_FINDING_ADDRESSED)
- Widen event_type max_length from 30 → 40
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0073_clean_stale_eva_findings"),
    ]

    operations = [
        migrations.AlterField(
            model_name="activitylog",
            name="event_type",
            field=models.CharField(
                choices=[
                    ("bank_upload", "Bank Statement Uploaded"),
                    ("classify_complete", "AI Classification Complete"),
                    ("classify_started", "AI Classification Started"),
                    ("tb_import", "Trial Balance Imported"),
                    ("tb_dup_merged", "TB Duplicate Accounts Merged"),
                    ("journal_posted", "Journal Entry Posted"),
                    ("year_finalised", "Financial Year Finalised"),
                    ("fy_status_changed", "Financial Year Status Changed"),
                    ("audit_run", "Audit Risk Analysis Run"),
                    ("review_approved", "Transactions Approved"),
                    ("doc_generated", "Document Generated"),
                    ("mgmt_accts_gen", "Management Accounts Generated"),
                    ("eva_review_triggered", "Eva Review Triggered"),
                    ("eva_review_cleared", "Eva Review Cleared"),
                    ("eva_finding_addressed", "Eva Finding Addressed"),
                    ("bas_commentary_generated", "BAS Commentary Generated"),
                    ("bas_commentary_edited", "BAS Commentary Edited"),
                    ("bas_commentary_regenerated", "BAS Commentary Regenerated"),
                    ("bas_commentary_sent", "BAS Commentary Sent"),
                    ("bas_commentary_deleted", "BAS Commentary Deleted"),
                    ("general", "General"),
                ],
                default="general",
                max_length=40,
            ),
        ),
        migrations.AddField(
            model_name="activitylog",
            name="eva_finding",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="activity_logs",
                to="core.evafinding",
            ),
        ),
        migrations.AddField(
            model_name="activitylog",
            name="metadata",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
