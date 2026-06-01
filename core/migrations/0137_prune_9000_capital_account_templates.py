"""Prune the 9000-series capital account templates (4000-family model).

Deactivates the 8 standard trust CapitalAccountTemplate rows seeded in
migration 0111. With these inactive, ``provision_capital_accounts``
(core/capital_account_service.py, which filters ``is_active=True``) no longer
creates 9001-9008 EntityChartOfAccount / ClientAccountMapping rows for newly
provisioned trust beneficiaries — new trusts get only the 4000-family accounts
(4000/4004/4053 ...) materialised by core/beneficiary_account_service.py.

Existing 9000-series rows on already-built trusts are NOT touched (the
templates are only the source for *new* provisioning). This is data-only and
reversible (reverse reactivates the templates).
"""

from django.db import migrations


def deactivate_9000_templates(apps, schema_editor):
    CapitalAccountTemplate = apps.get_model("core", "CapitalAccountTemplate")
    CapitalAccountTemplate.objects.filter(
        entity_type="trust", is_active=True
    ).update(is_active=False)


def reactivate_9000_templates(apps, schema_editor):
    CapitalAccountTemplate = apps.get_model("core", "CapitalAccountTemplate")
    CapitalAccountTemplate.objects.filter(
        entity_type="trust", is_active=False
    ).update(is_active=True)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0136_clientaccountmapping_target_entity_account"),
    ]

    operations = [
        migrations.RunPython(
            deactivate_9000_templates, reactivate_9000_templates
        ),
    ]
