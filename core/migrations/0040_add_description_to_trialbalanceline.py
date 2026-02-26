"""
Add description field to TrialBalanceLine for journal narrations.
Stores the journal description (from bulk upload column B or manual journal)
to display in the account code breakdown view.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0039_add_reopen_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="trialbalanceline",
            name="description",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Journal description or narration (from bulk upload column B or manual journal)",
                max_length=500,
            ),
        ),
    ]
