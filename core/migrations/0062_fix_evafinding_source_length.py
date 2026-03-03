"""Fix EvaFinding.source column length.

The DB column is varchar(10) but the model specifies max_length=15.
Values 'eva_analysis' (12 chars) and 'risk_engine' (11 chars) both
exceed varchar(10), causing DataError on every finding save.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0061_goingconcernassessment"),
    ]

    operations = [
        migrations.AlterField(
            model_name="evafinding",
            name="source",
            field=models.CharField(
                choices=[
                    ("risk_engine", "Risk Engine"),
                    ("eva_analysis", "Eva Analysis"),
                ],
                default="eva_analysis",
                help_text="Whether this finding originated from the risk engine or Eva's LLM analysis",
                max_length=15,
            ),
        ),
    ]
