import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0062_fix_evafinding_source_length"),
        ("review", "0009_backfill_posted_to_tb"),
    ]

    operations = [
        migrations.AddField(
            model_name="depreciationasset",
            name="source_transaction",
            field=models.ForeignKey(
                blank=True,
                help_text="Bank statement transaction this asset was created from",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="depreciation_assets",
                to="review.pendingtransaction",
            ),
        ),
    ]
