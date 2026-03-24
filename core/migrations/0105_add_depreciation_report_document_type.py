"""
Migration: Add DEPRECIATION_REPORT to FinancialStatementTemplate.DocumentType choices.

The Depreciation Report is generated programmatically from DepreciationAsset
records (no static .docx template is stored in the database for this type).
This migration only updates the choices field so the value is valid at the
database/model level. No data migration is required.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0104_merge_20260324_1114"),
    ]

    operations = [
        migrations.AlterField(
            model_name="financialstatementtemplate",
            name="document_type",
            field=models.CharField(
                choices=[
                    ("COVER", "Cover Page"),
                    ("DETAILED_PL", "Detailed Profit and Loss"),
                    ("BALANCE_SHEET", "Balance Sheet"),
                    ("SUMMARY_PL", "Summary P&L"),
                    ("DEPRECIATION_REPORT", "Depreciation Report"),
                    ("NOTES", "Notes to Financial Statements"),
                    ("DECLARATION", "Declaration"),
                    ("COMPILATION", "Compilation Report"),
                    ("DISTRIBUTION", "Distribution Summary"),
                ],
                default="COVER",
                max_length=20,
            ),
        ),
    ]
