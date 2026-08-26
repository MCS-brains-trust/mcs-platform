"""
Data migration: trust 4199 (Undistributed income) maps to BS-EQ-005, not
whatever a per-entity chart happened to acquire (observed live: one trust
had it pointed at BS-EQ-007, a partnership-only line).

Corrects:
  - ChartOfAccount (master trust template) — sets maps_to on the existing
    4199 row, or creates the row outright on a database that has never run
    `import_chart_of_accounts` (a fresh test database, for instance).
  - AccountMapping BS-EQ-005 — created if this database has never run
    `seed_account_mappings` (again, a fresh test database).
  - EntityChartOfAccount — every existing trust entity's 4199 row that
    isn't already on BS-EQ-005 is corrected.

get_or_create (not update_or_create) is used deliberately for the two
reference-data rows: on a database where they already exist (production),
this must not clobber fields we have no reason to touch. Only `maps_to` is
corrected on an existing ChartOfAccount row.
"""
from django.db import migrations


def map_trust_4199(apps, schema_editor):
    ChartOfAccount = apps.get_model("core", "ChartOfAccount")
    AccountMapping = apps.get_model("core", "AccountMapping")
    EntityChartOfAccount = apps.get_model("core", "EntityChartOfAccount")

    target, _ = AccountMapping.objects.get_or_create(
        standard_code="BS-EQ-005",
        defaults={
            "line_item_label": "Undistributed income",
            "financial_statement": "balance_sheet",
            "statement_section": "Equity",
            "display_order": 510,
            "applicable_entities": ["trust"],
        },
    )

    # Master template: correct the existing row in place, or create it for a
    # fresh database that hasn't been through import_chart_of_accounts.
    tpl, created = ChartOfAccount.objects.get_or_create(
        entity_type="trust", account_code="4199",
        defaults={
            "account_name": "Undistributed income",
            "classification": "Retained profits",
            "section": "pl_appropriation",
            "maps_to": target,
        },
    )
    if not created and tpl.maps_to_id != target.id:
        tpl.maps_to = target
        tpl.save(update_fields=["maps_to"])

    # Existing trust entity charts: correct anything not already on BS-EQ-005.
    EntityChartOfAccount.objects.filter(
        entity__entity_type="trust", account_code="4199",
    ).exclude(maps_to=target).update(maps_to=target)


def unmap(apps, schema_editor):
    ChartOfAccount = apps.get_model("core", "ChartOfAccount")
    ChartOfAccount.objects.filter(
        entity_type="trust", account_code="4199",
    ).update(maps_to=None)


class Migration(migrations.Migration):
    dependencies = [("core", "0147_evafindingsuppression_amount_at_suppression")]
    operations = [migrations.RunPython(map_trust_4199, unmap)]
