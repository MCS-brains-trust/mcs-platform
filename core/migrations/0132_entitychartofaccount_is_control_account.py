from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0131_alter_generalpool_assessable_income_adjustment_and_more"),
        ("core", "0131_entitychartofaccount_is_control_account"),
    ]

    operations = [
        migrations.AddField(
            model_name="entitychartofaccount",
            name="is_control_account",
            field=models.BooleanField(
                default=False,
                help_text="True if this is a control account with sub-accounts (e.g. 3523 with 3523.01, 3523.02)",
            ),
        ),
    ]
