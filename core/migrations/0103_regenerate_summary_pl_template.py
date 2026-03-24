"""
Migration: Regenerate the Summary P&L financial statement template.

Rebuilds the SUMMARY_PL docx template for company entities to include:
  - Operating profit before income tax          (bold, subtotal border)
  - Income tax attributable to operating profit (loss)
  - Operating profit after income tax           (bold, subtotal border)
  - Retained profit at the beginning of the financial year
  - Total available for appropriation           (bold, subtotal border)
  - Retained profits at the end of the financial year  (bold, grand-total border)

Context variables used:
  net_profit_pretax_cy/py, income_tax_cy/py, net_profit_cy/py,
  retained_profit_opening_cy/py, total_available_cy/py,
  retained_profit_closing_cy/py
"""
import io
import os

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import migrations


def regenerate_summary_pl(apps, schema_editor):
    """Rebuild the SUMMARY_PL template for company entities."""
    from core.management.commands.generate_fs_templates import _build_summary_pl

    FinancialStatementTemplate = apps.get_model("core", "FinancialStatementTemplate")

    entity_type = "company"
    doc_type = "SUMMARY_PL"

    template = FinancialStatementTemplate.objects.filter(
        document_type=doc_type,
        entity_type=entity_type,
        is_active=True,
    ).first()

    # Build the new docx in memory
    doc = _build_summary_pl(entity_type)
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)

    filename = f"{doc_type}_{entity_type}.docx"
    output_dir = os.path.join(settings.MEDIA_ROOT, "fs_templates", "defaults")
    os.makedirs(output_dir, exist_ok=True)

    if template:
        # Replace the existing file
        if template.template_file:
            try:
                template.template_file.delete(save=False)
            except Exception:
                pass
        template.template_file.save(filename, ContentFile(buf.read()), save=True)
    else:
        # Create a new record
        template = FinancialStatementTemplate(
            name="Summary P&L — Company",
            document_type=doc_type,
            entity_type=entity_type,
            version="1.0",
            is_active=True,
        )
        template.save()
        template.template_file.save(filename, ContentFile(buf.read()), save=True)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0102_alter_yearendcommentary_financial_year"),
    ]

    operations = [
        migrations.RunPython(regenerate_summary_pl, noop),
    ]
