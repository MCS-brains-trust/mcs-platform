# Generated migration for GeneralPool, GeneralPoolAsset, GeneralPoolDisposal
import uuid
import django.db.models.deletion
from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0129_add_knowledge_category_choices_wave1"),
    ]

    operations = [
        migrations.CreateModel(
            name="GeneralPool",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("financial_year", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="general_pool",
                    to="core.financialyear",
                )),
                ("opening_pool_balance", models.DecimalField(decimal_places=2, default=0, max_digits=15)),
                ("iawo_threshold", models.DecimalField(decimal_places=2, default=20000, max_digits=15)),
                ("total_additions", models.DecimalField(decimal_places=2, default=0, max_digits=15)),
                ("total_improvements", models.DecimalField(decimal_places=2, default=0, max_digits=15)),
                ("total_disposals", models.DecimalField(decimal_places=2, default=0, max_digits=15)),
                ("subtotal_e", models.DecimalField(decimal_places=2, default=0, max_digits=15)),
                ("low_pool_writeoff", models.BooleanField(default=False)),
                ("deduction_opening", models.DecimalField(decimal_places=2, default=0, max_digits=15)),
                ("deduction_additions", models.DecimalField(decimal_places=2, default=0, max_digits=15)),
                ("deduction_improvements", models.DecimalField(decimal_places=2, default=0, max_digits=15)),
                ("total_deduction", models.DecimalField(decimal_places=2, default=0, max_digits=15)),
                ("closing_pool_balance", models.DecimalField(decimal_places=2, default=0, max_digits=15)),
                ("assessable_income_adjustment", models.DecimalField(decimal_places=2, default=0, max_digits=15)),
                ("status", models.CharField(
                    choices=[("draft", "Draft"), ("calculated", "Calculated"), ("posted", "Posted to Trial Balance")],
                    default="draft",
                    max_length=20,
                )),
                ("posted_journal", models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="general_pool_postings",
                    to="core.adjustingjournal",
                )),
                ("dep_expense_code", models.CharField(blank=True, default="", max_length=20)),
                ("dep_expense_name", models.CharField(blank=True, default="", max_length=255)),
                ("pool_asset_code", models.CharField(blank=True, default="", max_length=20)),
                ("pool_asset_name", models.CharField(blank=True, default="", max_length=255)),
                ("assessable_income_code", models.CharField(blank=True, default="", max_length=20)),
                ("notes", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"verbose_name": "General Pool", "verbose_name_plural": "General Pools"},
        ),
        migrations.CreateModel(
            name="GeneralPoolAsset",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("pool", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="assets",
                    to="core.generalpool",
                )),
                ("asset_name", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True, default="")),
                ("date_first_used", models.DateField(blank=True, null=True)),
                ("cost", models.DecimalField(decimal_places=2, default=0, max_digits=15)),
                ("business_use_pct", models.DecimalField(decimal_places=2, default=100, max_digits=5)),
                ("is_improvement", models.BooleanField(default=False)),
                ("is_car", models.BooleanField(default=False)),
                ("car_limit_applied", models.DecimalField(decimal_places=2, default=0, max_digits=15)),
                ("notes", models.TextField(blank=True, default="")),
                ("display_order", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "verbose_name": "General Pool Asset",
                "verbose_name_plural": "General Pool Assets",
                "ordering": ["display_order", "date_first_used", "asset_name"],
            },
        ),
        migrations.CreateModel(
            name="GeneralPoolDisposal",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("pool", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="disposals",
                    to="core.generalpool",
                )),
                ("asset_name", models.CharField(max_length=255)),
                ("disposal_date", models.DateField(blank=True, null=True)),
                ("termination_value", models.DecimalField(decimal_places=2, default=0, max_digits=15)),
                ("business_use_pct", models.DecimalField(decimal_places=2, default=100, max_digits=5)),
                ("notes", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "verbose_name": "General Pool Disposal",
                "verbose_name_plural": "General Pool Disposals",
                "ordering": ["disposal_date", "asset_name"],
            },
        ),
    ]
