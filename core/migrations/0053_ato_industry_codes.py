"""
Replace the slug-based industry choices with 5-digit ATO Business Industry
Codes (NAT 1827-12.2021).

This migration:
  1. Adds a temporary ``industry_ato`` column (CharField max_length=5).
  2. Runs a data migration to convert existing slug values to their nearest
     ATO code using the mapping in ``core.industry_codes``.
  3. Removes the old ``industry`` column and renames ``industry_ato`` to
     ``industry``.
"""

from django.db import migrations, models


# Inline copy of the mapping so the migration is self-contained and does not
# depend on application code that may change in the future.
OLD_INDUSTRY_TO_ATO = {
    "accounting": "69320",
    "legal": "69310",
    "consulting": "69629",
    "it_services": "70000",
    "engineering": "69210",
    "architecture": "69210",
    "financial_services": "64190",
    "real_estate": "67200",
    "marketing": "69400",
    "professional_other": "69629",
    "medical_gp": "85110",
    "medical_specialist": "85122",
    "dental": "85310",
    "allied_health": "85391",
    "pharmacy": "42712",
    "veterinary": "69700",
    "healthcare_other": "85399",
    "construction": "30190",
    "electrical": "32310",
    "plumbing": "32320",
    "trades_other": "32410",
    "restaurant": "45110",
    "hotel": "44000",
    "catering": "45130",
    "food_manufacturing": "11990",
    "hospitality_other": "45110",
    "retail": "42799",
    "ecommerce": "43109",
    "wholesale": "38000",
    "transport": "46210",
    "courier": "51010",
    "agriculture": "01490",
    "mining": "09909",
    "fishing": "04130",
    "manufacturing": "24990",
    "nfp_charity": "95510",
    "nfp_association": "95510",
    "nfp_other": "95510",
    "education": "80100",
    "childcare": "87100",
    "property_investment": "67120",
    "investment": "64190",
    "smsf_industry": "63300",
    "beauty": "95391",
    "fitness": "91110",
    "cleaning": "73110",
    "security": "77120",
    "other": "",
}


def migrate_industry_forward(apps, schema_editor):
    Entity = apps.get_model("core", "Entity")
    for entity in Entity.objects.all():
        old_val = entity.industry or ""
        new_val = OLD_INDUSTRY_TO_ATO.get(old_val, "")
        entity.industry_ato = new_val
        entity.save(update_fields=["industry_ato"])


def migrate_industry_reverse(apps, schema_editor):
    """Best-effort reverse: map ATO codes back to old slugs."""
    ATO_TO_OLD = {v: k for k, v in OLD_INDUSTRY_TO_ATO.items() if v}
    Entity = apps.get_model("core", "Entity")
    for entity in Entity.objects.all():
        ato_val = entity.industry or ""
        old_val = ATO_TO_OLD.get(ato_val, "other")
        entity.industry_old = old_val
        entity.save(update_fields=["industry_old"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0052_add_unit_trust_ancillaries_and_transfer"),
    ]

    operations = [
        # Step 1: Add temporary column
        migrations.AddField(
            model_name="entity",
            name="industry_ato",
            field=models.CharField(
                blank=True,
                default="",
                max_length=5,
                help_text="ATO Business Industry Code (NAT 1827).",
            ),
        ),
        # Step 2: Copy & convert data
        migrations.RunPython(
            migrate_industry_forward,
            migrate_industry_reverse,
        ),
        # Step 3: Drop old column
        migrations.RemoveField(
            model_name="entity",
            name="industry",
        ),
        # Step 4: Rename new column
        migrations.RenameField(
            model_name="entity",
            old_name="industry_ato",
            new_name="industry",
        ),
        # Step 5: Set final field attributes
        migrations.AlterField(
            model_name="entity",
            name="industry",
            field=models.CharField(
                blank=True,
                default="",
                max_length=5,
                help_text="ATO Business Industry Code (NAT 1827) — used by Eva for AI analysis, GST coding, and benchmarking.",
            ),
        ),
    ]
