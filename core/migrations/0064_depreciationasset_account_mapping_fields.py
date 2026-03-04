from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0063_depreciationasset_source_transaction"),
    ]

    operations = [
        migrations.AddField(
            model_name="depreciationasset",
            name="asset_account_code",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Balance sheet account code where this asset sits (e.g. 2870)",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="depreciationasset",
            name="asset_account_name",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Balance sheet account name (e.g. Office equipment)",
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="depreciationasset",
            name="accum_dep_code",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Accumulated depreciation account code paired with this asset (e.g. 2875)",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="depreciationasset",
            name="accum_dep_name",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Accumulated depreciation account name (e.g. Less: Accumulated depreciation)",
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name="depreciationasset",
            name="dep_expense_code",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Depreciation expense account code (e.g. 1615)",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="depreciationasset",
            name="dep_expense_name",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Depreciation expense account name (e.g. Depreciation - Plant)",
                max_length=255,
            ),
        ),
    ]
