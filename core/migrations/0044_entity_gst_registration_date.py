"""
Migration: Add gst_registration_date to Entity model.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0043_bas_period_redesign"),
    ]

    operations = [
        migrations.AddField(
            model_name="entity",
            name="gst_registration_date",
            field=models.DateField(
                blank=True,
                null=True,
                help_text="Date GST registration commenced. Transactions before this date are auto-set to Out of Scope.",
            ),
        ),
    ]
