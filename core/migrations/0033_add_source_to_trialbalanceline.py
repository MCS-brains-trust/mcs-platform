from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0032_remap_income_tax_to_equity"),
    ]

    operations = [
        migrations.AddField(
            model_name="trialbalanceline",
            name="source",
            field=models.CharField(
                blank=True,
                choices=[
                    ("tb_import", "Trial Balance Import"),
                    ("bank_statement", "Bank Statement"),
                    ("manual_journal", "Manual Journal"),
                    ("rollover", "Rolled Forward"),
                ],
                default="tb_import",
                help_text="Where this line originated from",
                max_length=20,
            ),
        ),
    ]
