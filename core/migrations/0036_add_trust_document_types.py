"""Add Trust Election and Tax Planning Summary document types to GeneratedDocument."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0035_trust_tax_planning"),
    ]

    operations = [
        migrations.AlterField(
            model_name="generateddocument",
            name="document_type",
            field=models.CharField(
                choices=[
                    ("financial_statements", "Financial Statements"),
                    ("distribution_minutes", "Distribution Minutes"),
                    ("beneficiary_statement", "Beneficiary Statement"),
                    ("partner_statement", "Partner Statement"),
                    ("trust_election", "Trust Election (s97)"),
                    ("tax_planning_summary", "Tax Planning Summary"),
                    ("workpaper_notes", "Working Paper Notes"),
                    ("other", "Other"),
                ],
                default="financial_statements",
                max_length=30,
            ),
        ),
    ]
