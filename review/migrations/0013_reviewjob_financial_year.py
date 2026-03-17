import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
        ("review", "0012_merge_20260311_1214"),
    ]

    operations = [
        migrations.AddField(
            model_name="reviewjob",
            name="financial_year",
            field=models.ForeignKey(
                blank=True,
                help_text="Financial year this review job belongs to (enables cascade delete)",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="review_jobs",
                to="core.financialyear",
            ),
        ),
    ]
