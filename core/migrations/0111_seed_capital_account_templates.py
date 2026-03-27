"""Seed CapitalAccountTemplate with the 8 standard trust capital account line items."""

from django.db import migrations


SEED_DATA = [
    {"account_name": "Opening balance",               "classification": "Opening balance", "maps_to": "Undistributed income", "sort_order": 1},
    {"account_name": "Interest received on loan",     "classification": "Capital credits",  "maps_to": "Undistributed income", "sort_order": 2},
    {"account_name": "Funds loaned to trust",         "classification": "Capital credits",  "maps_to": "Undistributed income", "sort_order": 3},
    {"account_name": "Distribution for year",         "classification": "Capital credits",  "maps_to": "Undistributed income", "sort_order": 4},
    {"account_name": "Income tax withheld",           "classification": "Capital credits",  "maps_to": "Undistributed income", "sort_order": 5},
    {"account_name": "Advance maintenance/education", "classification": "Capital debits",   "maps_to": "Undistributed income", "sort_order": 6},
    {"account_name": "Interest on loan",              "classification": "Capital debits",   "maps_to": "Undistributed income", "sort_order": 7},
    {"account_name": "Physical distribution",         "classification": "Capital debits",   "maps_to": "Undistributed income", "sort_order": 8},
]


def seed_templates(apps, schema_editor):
    CapitalAccountTemplate = apps.get_model("core", "CapitalAccountTemplate")
    for row in SEED_DATA:
        CapitalAccountTemplate.objects.create(entity_type="trust", **row)


def unseed_templates(apps, schema_editor):
    CapitalAccountTemplate = apps.get_model("core", "CapitalAccountTemplate")
    CapitalAccountTemplate.objects.filter(entity_type="trust").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0110_officer_distribution_history_capital_accounts"),
    ]

    operations = [
        migrations.RunPython(seed_templates, unseed_templates),
    ]
