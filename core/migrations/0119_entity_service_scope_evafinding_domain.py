"""
Sprint 1b — Entity service scope flags + EvaFinding domain field.

Adds:
  - Entity.provides_financial_statements (Boolean, default True)
  - Entity.provides_rdti (Boolean, default False)
  - EvaFinding.domain (CharField, default 'financial_statements', indexed)

Existing rows back-fill to the defaults in a single UPDATE. No data migration
required — every entity is assumed to provide financial statements by default,
and every existing EvaFinding is assumed to belong to the financial_statements
domain.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0118_alter_rdtiapplication_aggregated_turnover_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="entity",
            name="provides_financial_statements",
            field=models.BooleanField(
                default=True,
                help_text="Entity uses StatementHub for financial statement preparation.",
            ),
        ),
        migrations.AddField(
            model_name="entity",
            name="provides_rdti",
            field=models.BooleanField(
                default=False,
                help_text="Entity uses StatementHub for R&D Tax Incentive registration drafting.",
            ),
        ),
        migrations.AddField(
            model_name="evafinding",
            name="domain",
            field=models.CharField(
                choices=[
                    ("financial_statements", "Financial Statements"),
                    ("rdti", "R&DTI"),
                ],
                db_index=True,
                default="financial_statements",
                help_text="Which compliance domain this finding belongs to.",
                max_length=30,
            ),
        ),
    ]
