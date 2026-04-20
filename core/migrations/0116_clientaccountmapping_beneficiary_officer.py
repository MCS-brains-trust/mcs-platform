import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0115_suppression_fingerprint_v2"),
    ]

    operations = [
        migrations.AddField(
            model_name="clientaccountmapping",
            name="beneficiary_officer",
            field=models.ForeignKey(
                blank=True,
                help_text="Beneficiary/unit holder this account belongs to (trust entities only).",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="client_account_mappings",
                to="core.entityofficer",
            ),
        ),
    ]
