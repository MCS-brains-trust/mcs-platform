import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0113_accountrangealias"),
    ]

    operations = [
        migrations.AddField(
            model_name="trustworkspace",
            name="selected_tax_scenario",
            field=models.ForeignKey(
                blank=True,
                help_text="TaxPlanningScenario chosen in Stage 2 for distribution posting.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="selected_for_workspaces",
                to="core.taxplanningscenario",
            ),
        ),
    ]
