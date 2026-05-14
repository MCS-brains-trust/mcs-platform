from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0130_generalpool_generalpoolasset_generalpooldisp"),
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
