"""
Add EntityChartOfAccount model for per-entity chart of accounts.
"""
import uuid
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0033_add_source_to_trialbalanceline"),
    ]

    operations = [
        migrations.CreateModel(
            name="EntityChartOfAccount",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "account_code",
                    models.CharField(
                        help_text='Account code, e.g. "0500", "1510", "2000.01"',
                        max_length=20,
                    ),
                ),
                (
                    "account_name",
                    models.CharField(
                        help_text='Account name, e.g. "Sales", "Accountancy"',
                        max_length=255,
                    ),
                ),
                (
                    "classification",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text='Tax classification, e.g. "Other sales revenue"',
                        max_length=255,
                    ),
                ),
                (
                    "section",
                    models.CharField(
                        choices=[
                            ("suspense", "Suspense"),
                            ("revenue", "Revenue"),
                            ("cost_of_sales", "Cost of Sales"),
                            ("expenses", "Expenses"),
                            ("assets", "Assets"),
                            ("liabilities", "Liabilities"),
                            ("equity", "Equity"),
                            ("capital_accounts", "Capital Accounts"),
                            ("pl_appropriation", "P&L Appropriation"),
                        ],
                        help_text="Which section of the financial statements this belongs to",
                        max_length=30,
                    ),
                ),
                (
                    "tax_code",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="Default tax code: GST, ADS, ITS, FRE, CAP, INP, etc.",
                        max_length=20,
                    ),
                ),
                ("is_active", models.BooleanField(default=True)),
                (
                    "is_custom",
                    models.BooleanField(
                        default=False,
                        help_text="True if this account was added by the accountant (not from template)",
                    ),
                ),
                ("display_order", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "entity",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="entity_accounts",
                        to="core.entity",
                    ),
                ),
                (
                    "maps_to",
                    models.ForeignKey(
                        blank=True,
                        help_text="Which financial statement line item this rolls up to",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="entity_detailed_accounts",
                        to="core.accountmapping",
                    ),
                ),
            ],
            options={
                "ordering": ["section", "account_code"],
                "unique_together": {("entity", "account_code")},
                "indexes": [
                    models.Index(
                        fields=["entity", "section"],
                        name="core_entityc_entity__idx_sec",
                    ),
                    models.Index(
                        fields=["entity", "is_active"],
                        name="core_entityc_entity__idx_act",
                    ),
                ],
            },
        ),
    ]
