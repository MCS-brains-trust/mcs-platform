"""
Remap Income Tax (IS-TAX-001) from income_statement to balance_sheet / Equity.

This changes account 4110 (Income tax on profit) so that:
- It appears in the Equity section of the Balance Sheet
- It carries forward during year rollover as a BS item
- Its value is absorbed into Retained Profits during rollover
"""

from django.db import migrations


def remap_income_tax(apps, schema_editor):
    AccountMapping = apps.get_model("core", "AccountMapping")
    try:
        tax_mapping = AccountMapping.objects.get(standard_code="IS-TAX-001")
        # Update to new standard code and reclassify
        tax_mapping.standard_code = "BS-EQ-011"
        tax_mapping.line_item_label = "Income tax provision"
        tax_mapping.financial_statement = "balance_sheet"
        tax_mapping.statement_section = "Equity"
        tax_mapping.display_order = 540  # After dividends (530)
        tax_mapping.save()
    except AccountMapping.DoesNotExist:
        # If IS-TAX-001 doesn't exist, create BS-EQ-011 directly
        AccountMapping.objects.create(
            standard_code="BS-EQ-011",
            line_item_label="Income tax provision",
            financial_statement="balance_sheet",
            statement_section="Equity",
            display_order=540,
        )


def reverse_remap(apps, schema_editor):
    AccountMapping = apps.get_model("core", "AccountMapping")
    try:
        tax_mapping = AccountMapping.objects.get(standard_code="BS-EQ-011")
        tax_mapping.standard_code = "IS-TAX-001"
        tax_mapping.line_item_label = "Income tax expense"
        tax_mapping.financial_statement = "income_statement"
        tax_mapping.statement_section = "Income Tax"
        tax_mapping.display_order = 900
        tax_mapping.save()
    except AccountMapping.DoesNotExist:
        pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0027_entity_primary_accountant_entity_reviewer_and_more"),
    ]

    operations = [
        migrations.RunPython(remap_income_tax, reverse_remap),
    ]
