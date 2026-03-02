"""
Add prior_commentary FK to BASPeriodCommentary for trend chaining,
and add BAS commentary event types to ActivityLog.
"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0058_asicreturn_basperiodcommentary_correspondence_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="basperiodcommentary",
            name="prior_commentary",
            field=models.ForeignKey(
                blank=True,
                help_text="Link to the prior period's commentary for trend chaining and comparison",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="subsequent_commentaries",
                to="core.basperiodcommentary",
            ),
        ),
        migrations.AlterField(
            model_name="activitylog",
            name="event_type",
            field=models.CharField(
                choices=[
                    ("bank_upload", "Bank Statement Uploaded"),
                    ("classify_complete", "AI Classification Complete"),
                    ("classify_started", "AI Classification Started"),
                    ("tb_import", "Trial Balance Imported"),
                    ("journal_posted", "Journal Entry Posted"),
                    ("year_finalised", "Financial Year Finalised"),
                    ("audit_run", "Audit Risk Analysis Run"),
                    ("review_approved", "Transactions Approved"),
                    ("doc_generated", "Document Generated"),
                    ("bas_commentary_generated", "BAS Commentary Generated"),
                    ("bas_commentary_edited", "BAS Commentary Edited"),
                    ("bas_commentary_regenerated", "BAS Commentary Regenerated"),
                    ("bas_commentary_sent", "BAS Commentary Sent"),
                    ("bas_commentary_deleted", "BAS Commentary Deleted"),
                    ("general", "General"),
                ],
                default="general",
                max_length=30,
            ),
        ),
    ]
