"""
Migration for Enhanced Transaction Review Workflow.
Adds:
- ClassificationRule model (entity-scoped rule memory)
- EntityGSTSetting model (per-entity GST apportionment settings)
- New fields on PendingTransaction (gst_treatment, creditable_percentage,
  gst_amount_override, gst_override_reason, is_gst_manual, matched_rule,
  is_split, split_parent, split_line_number)
- gst_registration_date on Entity (core app)
"""
import uuid
import django.db.models.deletion
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("review", "0005_alter_reviewactivity_activity_type_and_more"),
        ("core", "0043_bas_period_redesign"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # --- ClassificationRule ---
        migrations.CreateModel(
            name="ClassificationRule",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("description_pattern", models.CharField(help_text="The keyword or phrase to match against transaction descriptions", max_length=500)),
                ("match_type", models.CharField(choices=[("exact", "Exact Phrase"), ("contains", "Contains")], default="contains", help_text="'exact' matches the full description, 'contains' matches substring", max_length=10)),
                ("account_code", models.CharField(max_length=20)),
                ("account_name", models.CharField(max_length=255)),
                ("gst_treatment", models.CharField(blank=True, choices=[("", "-- Select --"), ("taxable", "Taxable (GST)"), ("gst_free", "GST-Free"), ("input_taxed", "Input Taxed"), ("out_of_scope", "Out of Scope"), ("not_registered", "Not Registered")], default="", help_text="GST treatment to apply when rule matches", max_length=20)),
                ("creditable_percentage", models.DecimalField(decimal_places=2, default=100, help_text="Creditable percentage to apply (0-100)", max_digits=5)),
                ("is_active", models.BooleanField(default=True, help_text="Inactive rules are ignored during matching")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("entity", models.ForeignKey(help_text="Rules are strictly entity-scoped (no cross-entity bleed)", on_delete=django.db.models.deletion.CASCADE, related_name="classification_rules", to="core.entity")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-created_at"],
                "verbose_name": "Classification Rule",
                "verbose_name_plural": "Classification Rules",
            },
        ),
        # --- EntityGSTSetting ---
        migrations.CreateModel(
            name="EntityGSTSetting",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("setting_type", models.CharField(choices=[("vehicle_business_use", "Vehicle Business Use %"), ("home_office_use", "Home Office Business Use %"), ("phone_business_use", "Phone/Internet Business Use %"), ("entertainment_method", "Entertainment FBT Method"), ("custom", "Custom Apportionment")], max_length=30)),
                ("value", models.CharField(help_text="The setting value (percentage as string, method name, etc.)", max_length=100)),
                ("label", models.CharField(blank=True, default="", help_text="Human-readable label for custom settings", max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("entity", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="gst_settings", to="core.entity")),
                ("financial_year", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="entity_gst_settings", to="core.financialyear")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-created_at"],
                "verbose_name": "Entity GST Setting",
                "verbose_name_plural": "Entity GST Settings",
                "unique_together": {("entity", "financial_year", "setting_type")},
            },
        ),
        # --- PendingTransaction new fields ---
        migrations.AddField(
            model_name="pendingtransaction",
            name="gst_treatment",
            field=models.CharField(blank=True, choices=[("", "-- Select --"), ("taxable", "Taxable (GST)"), ("gst_free", "GST-Free"), ("input_taxed", "Input Taxed"), ("out_of_scope", "Out of Scope"), ("not_registered", "Not Registered")], default="", help_text="Simplified GST treatment for BAS purposes", max_length=20),
        ),
        migrations.AddField(
            model_name="pendingtransaction",
            name="creditable_percentage",
            field=models.DecimalField(decimal_places=2, default=100, help_text="Creditable percentage for ITC calculation (0-100)", max_digits=5),
        ),
        migrations.AddField(
            model_name="pendingtransaction",
            name="gst_amount_override",
            field=models.DecimalField(blank=True, decimal_places=2, help_text="Direct GST amount override by accountant", max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name="pendingtransaction",
            name="gst_override_reason",
            field=models.TextField(blank=True, default="", help_text="Mandatory reason when GST amount is directly overridden"),
        ),
        migrations.AddField(
            model_name="pendingtransaction",
            name="is_gst_manual",
            field=models.BooleanField(default=False, help_text="True if GST treatment was manually set (vs AI default)"),
        ),
        migrations.AddField(
            model_name="pendingtransaction",
            name="matched_rule",
            field=models.ForeignKey(blank=True, help_text="Classification rule that was applied to this transaction", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="matched_transactions", to="review.classificationrule"),
        ),
        migrations.AddField(
            model_name="pendingtransaction",
            name="is_split",
            field=models.BooleanField(default=False, help_text="True if this transaction has been split into sub-lines"),
        ),
        migrations.AddField(
            model_name="pendingtransaction",
            name="split_parent",
            field=models.ForeignKey(blank=True, help_text="If this is a split line, points to the original transaction", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="split_children", to="review.pendingtransaction"),
        ),
        migrations.AddField(
            model_name="pendingtransaction",
            name="split_line_number",
            field=models.PositiveIntegerField(blank=True, help_text="Line number within a split (1, 2, 3...)", null=True),
        ),
        # --- Entity.gst_registration_date ---
        migrations.AddField(
            model_name="entity",
            name="gst_registration_date",
            field=models.DateField(blank=True, help_text="Date GST registration commenced. Transactions before this date are auto-set to Out of Scope.", null=True),
        ),
    ]
