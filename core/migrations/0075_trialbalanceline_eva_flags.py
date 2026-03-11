from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0074_extend_activity_log"),
    ]

    operations = [
        migrations.AddField(
            model_name="trialbalanceline",
            name="eva_flags",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="List of EvaFinding check_name strings that flagged this row",
            ),
        ),
    ]
