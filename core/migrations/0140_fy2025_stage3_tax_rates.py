"""Correct the FY2025 TaxReferenceData seed to Stage 3 tax law.

Migration 0035 seeded FY2025 (year ended 30 June 2025) with the superseded
pre-Stage-3 individual scale (19% / 32.5% / 37% / 45%; $45k / $120k / $180k).
The Stage 3 cuts apply from 1 July 2024, so FY2025 must use:

    $0–$18,200         nil
    $18,201–$45,000    16%
    $45,001–$135,000   30%
    $135,001–$190,000  37%
    $190,001+          45%

Because get_tax_rates() (core/tax_engine.py) is DB-first, the stale seeded
rows silently overrode the correct hardcoded fallback on every migrated
database — overstating individual beneficiary tax on trust tax-planning
worksheets for FY2025.

Also updates the Medicare levy low-income threshold to the 2024-25 value
($27,222 for singles; was seeded with the 2023-24 value of $26,000).

Reverse restores the pre-Stage-3 values exactly as 0035 seeded them.
"""
from django.db import migrations


# (key, correct value, correct description, stale seeded value, stale description)
_FY2025_CORRECTIONS = [
    ("bracket_1_rate", "0.16", "16% on $18,201–$45,000",
     "0.19", "19% on $18,201–$45,000"),
    ("bracket_2_rate", "0.30", "30% on $45,001–$135,000",
     "0.325", "32.5% on $45,001–$120,000"),
    ("bracket_2_upper", "135000", "Upper limit of 30% bracket",
     "120000", "Upper limit of 32.5% bracket"),
    ("bracket_3_rate", "0.37", "37% on $135,001–$190,000",
     "0.37", "37% on $120,001–$180,000"),
    ("bracket_3_upper", "190000", "Upper limit of 37% bracket",
     "180000", "Upper limit of 37% bracket"),
    ("bracket_4_rate", "0.45", "45% on $190,001+",
     "0.45", "45% on $180,001+"),
    ("medicare_low_income_threshold", "27222",
     "2024-25 Medicare levy low-income threshold (singles)",
     "26000", "Below this: reduced Medicare or nil"),
]


def _apply(apps, schema_editor):
    TaxReferenceData = apps.get_model("core", "TaxReferenceData")
    for key, value, desc, _stale_value, _stale_desc in _FY2025_CORRECTIONS:
        TaxReferenceData.objects.update_or_create(
            financial_year_label="FY2025",
            key=key,
            defaults={"value": value, "description": desc},
        )


def _reverse(apps, schema_editor):
    TaxReferenceData = apps.get_model("core", "TaxReferenceData")
    for key, _value, _desc, stale_value, stale_desc in _FY2025_CORRECTIONS:
        TaxReferenceData.objects.update_or_create(
            financial_year_label="FY2025",
            key=key,
            defaults={"value": stale_value, "description": stale_desc},
        )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0139_depreciation_source_tags"),
    ]

    operations = [
        migrations.RunPython(_apply, _reverse),
    ]
