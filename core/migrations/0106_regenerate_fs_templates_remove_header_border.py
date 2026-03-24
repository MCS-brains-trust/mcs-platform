"""
Migration 0106: Regenerate all financial statement templates to remove the
top border line above the year column headers (2025 / 2024).
"""
from django.db import migrations


def regenerate_templates(apps, schema_editor):
    try:
        from core.management.commands.generate_fs_templates import Command
        cmd = Command()
        cmd.handle(force=True, entity_type=None, doc_type=None, verbosity=0)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(
            "Could not regenerate FS templates in migration 0106: %s", e
        )


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0105_add_depreciation_report_document_type'),
    ]

    operations = [
        migrations.RunPython(regenerate_templates, migrations.RunPython.noop),
    ]
