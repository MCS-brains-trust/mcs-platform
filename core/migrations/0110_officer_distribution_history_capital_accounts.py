import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0109_stagedimport"),
    ]

    operations = [
        # --- OfficerDistributionHistory ---
        migrations.CreateModel(
            name="OfficerDistributionHistory",
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
                    "distribution_pct",
                    models.DecimalField(decimal_places=2, max_digits=6),
                ),
                ("effective_from", models.DateField()),
                ("effective_to", models.DateField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "officer",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="distribution_history",
                        to="core.entityofficer",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Distribution History",
                "verbose_name_plural": "Distribution Histories",
                "ordering": ["-effective_from"],
            },
        ),
        # --- CapitalAccountTemplate ---
        migrations.CreateModel(
            name="CapitalAccountTemplate",
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
                    "entity_type",
                    models.CharField(
                        choices=[("trust", "Trust")],
                        help_text="Entity type this template applies to (trust types only).",
                        max_length=20,
                    ),
                ),
                (
                    "account_name",
                    models.CharField(
                        help_text='Template account name, e.g. "Opening balance", "Distribution for year"',
                        max_length=200,
                    ),
                ),
                (
                    "classification",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text='e.g. "Opening balance", "Capital credits", "Capital debits"',
                        max_length=200,
                    ),
                ),
                (
                    "maps_to",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="Financial statement line item this maps to",
                        max_length=200,
                    ),
                ),
                (
                    "sort_order",
                    models.PositiveIntegerField(
                        default=0,
                        help_text="Display order within the capital section",
                    ),
                ),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "verbose_name": "Capital Account Template",
                "verbose_name_plural": "Capital Account Templates",
                "ordering": ["entity_type", "sort_order"],
            },
        ),
        # --- EntityChartOfAccount new fields ---
        migrations.AddField(
            model_name="entitychartofaccount",
            name="beneficiary_officer",
            field=models.ForeignKey(
                blank=True,
                help_text="Unit holder or beneficiary this capital account belongs to",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="capital_accounts_coa",
                to="core.entityofficer",
            ),
        ),
        migrations.AddField(
            model_name="entitychartofaccount",
            name="capital_template_item",
            field=models.ForeignKey(
                blank=True,
                help_text="Template line item that generated this account",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="provisioned_accounts",
                to="core.capitalaccounttemplate",
            ),
        ),
        migrations.AddField(
            model_name="entitychartofaccount",
            name="auto_provisioned",
            field=models.BooleanField(
                default=False,
                help_text="True if created by the capital account auto-provisioning engine",
            ),
        ),
        migrations.AddField(
            model_name="entitychartofaccount",
            name="is_ceased",
            field=models.BooleanField(
                default=False,
                help_text="True if the linked beneficiary/unit holder has ceased",
            ),
        ),
    ]
