from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0132_entitychartofaccount_is_control_account"),
    ]

    operations = [
        migrations.AlterField(
            model_name="trialbalanceline",
            name="account_code",
            field=models.CharField(max_length=50),
        ),
        migrations.AlterField(
            model_name="clientaccountmapping",
            name="client_account_code",
            field=models.CharField(max_length=50),
        ),
        migrations.AlterField(
            model_name="journalline",
            name="account_code",
            field=models.CharField(max_length=50),
        ),
    ]
