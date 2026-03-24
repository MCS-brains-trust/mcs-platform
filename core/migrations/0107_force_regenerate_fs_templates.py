"""
Migration 0107: Force-regenerate all financial statement templates.

0106 ran before the header border fix code was deployed to the server,
so templates were rebuilt from the old code. This migration re-runs the
regeneration now that the correct code (no top border on header rows) is live.
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
            "Could not regenerate FS templates in migration 0107: %s", e
        )


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0106_regenerate_fs_templates_remove_header_border'),
    ]

    operations = [
        migrations.RunPython(regenerate_templates, migrations.RunPython.noop),
    ]
