import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0054_add_company_establishment"),
    ]

    operations = [
        migrations.CreateModel(
            name="BankAccountMapping",
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
                    "bank_account_name",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="Bank account name from the statement (e.g. 'CBA Business Account')",
                        max_length=255,
                    ),
                ),
                (
                    "bsb",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="BSB number (e.g. '062-000')",
                        max_length=20,
                    ),
                ),
                (
                    "account_number",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="Account number (e.g. '12345678')",
                        max_length=50,
                    ),
                ),
                (
                    "tb_account_code",
                    models.CharField(
                        help_text="Trial balance account code for this bank (e.g. '1100')",
                        max_length=20,
                    ),
                ),
                (
                    "tb_account_name",
                    models.CharField(
                        help_text="Trial balance account name (e.g. 'Cash at Bank - CBA')",
                        max_length=255,
                    ),
                ),
                (
                    "is_default",
                    models.BooleanField(
                        default=False,
                        help_text="If True, this is the default bank account for the entity",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True),
                ),
                (
                    "entity",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="bank_account_mappings",
                        to="core.entity",
                    ),
                ),
                (
                    "mapped_line_item",
                    models.ForeignKey(
                        blank=True,
                        help_text="Financial statement line item this bank account maps to",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="bank_account_mappings",
                        to="core.accountmapping",
                    ),
                ),
            ],
            options={
                "ordering": ["entity", "tb_account_code"],
                "unique_together": {("entity", "bsb", "account_number")},
                "indexes": [
                    models.Index(
                        fields=["entity", "bsb", "account_number"],
                        name="core_bankac_entity__idx",
                    ),
                ],
            },
        ),
    ]
