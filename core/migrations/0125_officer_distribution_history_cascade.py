"""Fix entity-delete HTTP 500 — reclassify OfficerDistributionHistory.officer
on_delete from PROTECT to CASCADE.

Pre-existing bug: deleting an Entity cascaded to EntityOfficer (CASCADE), then
the cascade-delete of the officer was blocked by OfficerDistributionHistory.officer
which was on_delete=PROTECT. Result: ProtectedError surfaced as HTTP 500 from
client_bulk_action.

Phase 1 audit (2026-04-29) found this is the ONLY PROTECT FK in Entity's
downstream subgraph. Distribution history is a per-officer audit trail and
should die with its officer — every other officer-pointing FK already cascades,
and entity_officer_delete pre-clears these rows manually as a workaround.

Refs: entity_delete_phase1_findings.md
"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0124_register_coa_hygiene_periodic_task'),
    ]

    operations = [
        migrations.AlterField(
            model_name='officerdistributionhistory',
            name='officer',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='distribution_history',
                to='core.entityofficer',
            ),
        ),
    ]
